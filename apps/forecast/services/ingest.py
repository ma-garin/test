"""AH-05: Signal 受信から予測再計算までの、冪等なイベント処理。

プロダクト内のエージェント・ループは、常駐 AI が自由に考え続けるものではなく、
イベントと時間を起点にした有限のループとして実装する。

    TRIGGER → OBSERVE → NORMALIZE → LINK → RECOMPUTE → ASSESS → PROPOSE → HUMAN REVIEW

このモジュールは OBSERVE から RECOMPUTE までを担う。PROPOSE 以降（通知の下書き、
人の採否）は別モジュールで扱い、ここでは外部システムへ一切書き込まない。

不変条件:
- 起動条件は、認証済みの外部イベント・同期の成否・明示更新・定時ジョブだけ。
  任意のチャット入力からは起動しない。
- `source` + `external_event_id`、無ければ内容ハッシュで冪等化する。
  再送では Signal も Snapshot も通知候補も増えない。
- 会話（Slack 等）は候補であり、状態・期日を確定しない。
- 失敗は握りつぶさず、受付記録へ理由を残す。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.forecast.models.inbound import InboundEvent
from apps.forecast.models.signals import Signal, SignalClassification, SignalSource
from apps.forecast.models.snapshots import ForecastSnapshot
from apps.forecast.services.recompute import RecomputeResult, recompute_project

#: 会話由来は候補にとどめる分類。予測の確定根拠には使わない。
CANDIDATE_ONLY = (SignalClassification.CONVERSATION,)

#: 予測の再計算を起こす分類。会話やコミットだけでは日付を動かさない。
RECOMPUTE_TRIGGERS = (
    SignalClassification.DEFECT_REPORTED,
    SignalClassification.DEFECT_UPDATED,
    SignalClassification.ISSUE_UPDATED,
    SignalClassification.TEST_FAILED,
    SignalClassification.TEST_PASSED,
    SignalClassification.SCHEDULE_UPDATE,
)


class IngestError(RuntimeError):
    """正規化できないイベント。受付記録に理由を残してから送出する。"""


@dataclass(frozen=True)
class IngestResult:
    """1 イベントの処理結果。重複と新規を必ず区別できる形で返す。"""

    event: InboundEvent
    signal: Signal | None = None
    recompute: RecomputeResult | None = None
    is_duplicate: bool = False

    @property
    def created_snapshots(self) -> tuple[ForecastSnapshot, ...]:
        return self.recompute.created if self.recompute else ()

    @property
    def notifications(self) -> tuple[ForecastSnapshot, ...]:
        """通知の候補。悪化と算定不能化だけを対象にし、通常更新で乱発しない。"""

        if self.recompute is None:
            return ()
        return (*self.recompute.worsened, *self.recompute.became_undeterminable)


def receive_event(
    project,
    *,
    source: str,
    event_type: str,
    occurred_at: datetime,
    payload: dict,
    external_event_id: str = "",
    summary: str = "",
    permalink: str = "",
    classification: str = SignalClassification.OTHER,
    excerpt: str = "",
    channel_reference: str = "",
    author_reference: str = "",
    recompute: bool = True,
) -> IngestResult:
    """外部イベントを受け付け、正規化し、影響範囲だけを再計算する。

    同じイベントが再送されても、Signal・Snapshot・通知候補は増えない。
    """

    payload_hash = InboundEvent.compute_hash(payload)
    existing = _find_existing(project, source, external_event_id, payload_hash)
    if existing is not None:
        duplicate = InboundEvent.objects.create(
            project=project,
            source=source,
            external_event_id=external_event_id,
            event_type=event_type,
            payload_hash=payload_hash,
            occurred_at=occurred_at,
            status=InboundEvent.Status.DUPLICATE,
            duplicate_of=existing,
            signal=existing.signal,
        )
        return IngestResult(event=duplicate, signal=existing.signal, is_duplicate=True)

    event = InboundEvent.objects.create(
        project=project,
        source=source,
        external_event_id=external_event_id,
        event_type=event_type,
        payload_hash=payload_hash,
        occurred_at=occurred_at,
    )

    # 受付記録は確定させ、正規化以降だけをセーブポイントで包む。
    # 全体を 1 トランザクションにすると、失敗時に「失敗した記録」ごと消える。
    try:
        with transaction.atomic():
            signal = _normalize(
                project,
                event=event,
                source=source,
                classification=classification,
                summary=summary or event_type,
                permalink=permalink,
                excerpt=excerpt,
                channel_reference=channel_reference,
                author_reference=author_reference,
            )
            event.signal = signal
            event.status = InboundEvent.Status.PROCESSED
            event.save(update_fields=["signal", "status", "updated_at"])

            result = None
            if recompute and classification in RECOMPUTE_TRIGGERS:
                result = recompute_project(project, timezone.localdate(), evidence=[signal])
    except Exception as error:  # 失敗を握りつぶさない
        event.status = InboundEvent.Status.FAILED
        event.error_reason = str(error)[:300]
        event.save(update_fields=["status", "error_reason", "updated_at"])
        raise IngestError(str(error)) from error

    return IngestResult(event=event, signal=signal, recompute=result)


def revoke_signal(signal: Signal, *, reason: str = "") -> Signal:
    """外部で削除・権限変更された Signal を無効化する。

    静かに残し続けない。根拠としての有効性を落とし、次の再計算から外れるようにする。
    物理削除しないのは、過去の予測が何を根拠にしたかを追えなくなるため。
    """

    signal.is_revoked = True
    signal.save(update_fields=["is_revoked", "updated_at"])
    return signal


def _find_existing(project, source: str, external_event_id: str, payload_hash: str):
    """冪等化の鍵。外部IDを第一に、無ければ内容ハッシュで探す。"""

    query = InboundEvent.objects.filter(project=project, source=source).exclude(
        status=InboundEvent.Status.DUPLICATE
    )
    if external_event_id:
        found = query.filter(external_event_id=external_event_id).first()
        if found is not None:
            return found
    return query.filter(payload_hash=payload_hash).first()


def _normalize(project, *, event, source, classification, summary, **fields) -> Signal:
    """製品固有のペイロードを Signal へ変換する。原文は複製せずリンクで残す。"""

    if source not in SignalSource.values:
        raise ValueError(f"未対応の情報源です: {source}")
    if classification not in SignalClassification.values:
        raise ValueError(f"未対応の分類です: {classification}")

    signal_hash = Signal.compute_hash(source, event.external_event_id, event.payload_hash)
    existing = Signal.objects.filter(
        project=project, source=source, payload_hash=signal_hash
    ).first()
    if existing is not None:
        return existing

    return Signal.objects.create(
        project=project,
        source=source,
        external_id=event.external_event_id,
        classification=classification,
        occurred_at=event.occurred_at,
        summary=summary[:300],
        payload_hash=signal_hash,
        **fields,
    )
