"""予兆検知アラートの一覧表示と、人の判断（確認・解消・対象外）の記録。

これまでアラートは検知して保存するだけで、状態を更新する経路が Django admin
にしか無かった。その結果、次の 3 つが同時に壊れていた。

1. `Alert.lead_time_days` が常に None になり、PoC 受入条件「予兆検知の
   先行日数」を実測できない（検知した時点と人が気づいた時点の差が測れない）
2. 未対応アラートが永久に残り、ヘルススコアを恒久的に下げ続ける
   （`overview.PENALTY_OPEN_ALERT`）
3. 重複排除（`detection.runner.ACTIVE_ALERT_STATUSES`）により、未対応のまま
   残った対象は二度と再検知されない

つまり「確認した」を押せることは表示上の親切ではなく、検知機能そのものの
前提条件になっている。ここでは表示（AlertBoard）と書き込み（decide_alert）を
1 モジュールにまとめ、状態遷移の規則を 1 か所だけに置く。

判断は履歴なので上書きを許さない。更新は `QuerySet.update()` で行い、渡された
インスタンスは書き換えない（呼び出し側が古い値と新しい値を比較できるように
するため）。`apps/dashboard/services/interventions.py` と同じ作法。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from apps.audit.models import OperationLog
from apps.dashboard.models import Alert

#: 操作ログに残すときの操作名。
DECIDE_ACTION = "alert.decide"

#: 人が選べる判断。未対応（open）へは戻せない。
DECIDABLE_STATUSES: tuple[str, ...] = (
    Alert.Status.ACKNOWLEDGED,
    Alert.Status.RESOLVED,
    Alert.Status.DISMISSED,
)

#: 判断ごとに、その判断を行える元の状態。
#:
#: 「確認した」は未対応のときだけ意味を持つ（確認済みをもう一度確認しても
#: 記録として何も増えない）。解消・対象外は、確認を挟んでも挟まなくても
#: 打てるようにする。現場では「見た瞬間に対応済みだと分かる」ことがあり、
#: 二段階を強制すると余計なクリックのために状態が正確でなくなる。
SOURCE_STATUSES: dict[str, tuple[str, ...]] = {
    Alert.Status.ACKNOWLEDGED: (Alert.Status.OPEN,),
    Alert.Status.RESOLVED: (Alert.Status.OPEN, Alert.Status.ACKNOWLEDGED),
    Alert.Status.DISMISSED: (Alert.Status.OPEN, Alert.Status.ACKNOWLEDGED),
}

#: 判断メモを必須にする状態。「対象外」は AI の検知を人が否定する判断なので、
#: 理由が残っていないと、後から誤検知だったのか見落としだったのか分からない。
REASON_REQUIRED_STATUSES: tuple[str, ...] = (Alert.Status.DISMISSED,)

#: 根拠（evidence）のキーを日本語にする対応表。
#: 検知器が積む値をそのまま出すと `severe_ratio_percent: 40` のような英字の
#: 羅列になり、「AIが何か言っている」だけで対応の判断に使えない。
#: 表に無いキーはキー名のまま出す（黙って隠すと根拠が欠けたことに気づけない）。
EVIDENCE_LABELS: dict[str, str] = {
    "delay_days": "遅延日数",
    "impacted_tasks": "影響タスク数",
    "is_critical_path": "クリティカルパス",
    "min_impacted_tasks": "影響タスク数の下限",
    "max_depth": "追跡する深さ",
    "signal_count": "兆候の数",
    "min_signals": "兆候数の下限",
    "stale_update_days": "未更新とみなす日数",
    "same_ball_holder_days": "同一担当の滞留日数",
    "low_progress_percent": "低進捗とみなす割合(%)",
    "window_count": "対象期間の件数",
    "window_days": "対象期間(日)",
    "severe_count": "重大な件数",
    "severe_percent": "重大の割合(%)",
    "severe_ratio_percent": "重大の割合しきい値(%)",
    "open_count": "未クローズ件数",
    "open_percent": "未クローズの割合(%)",
    "open_ratio_percent": "未クローズ割合のしきい値(%)",
    "spike_ratio": "急増とみなす倍率",
    "ratio": "基準期間との比",
    "baseline_rate": "基準期間の発生率",
    "window_rate": "対象期間の発生率",
    "min_observations": "判定に必要な観測数",
    "change_count": "変更要求の件数",
    "min_changes": "変更要求の下限",
    "recent_days": "直近の日数",
    "baseline_days": "基準期間(日)",
}


class AlreadyDecidedError(RuntimeError):
    """すでに確定済みのアラートを、もう一度更新しようとした。"""


class InvalidDecisionError(ValueError):
    """判断として成立しない入力。"""


# --- 表示 -------------------------------------------------------------------


@dataclass(frozen=True)
class AlertFilters:
    """一覧で選択中の絞り込み条件。フォームの選択状態の復元に使う。"""

    status: str = ""
    severity: str = ""
    category: str = ""

    @property
    def is_active(self) -> bool:
        return any([self.status, self.severity, self.category])

    @property
    def status_choices(self) -> list[tuple[str, str]]:
        return Alert.Status.choices

    @property
    def severity_choices(self) -> list[tuple[str, str]]:
        return Alert.Severity.choices

    @property
    def category_choices(self) -> list[tuple[str, str]]:
        return Alert.Category.choices


@dataclass(frozen=True)
class AlertRow:
    """1 アラートの表示用データ。

    「何を根拠に、どのしきい値を超えたと言っているのか」を行の中で読み切れる
    ようにする。根拠が読めないアラートは対応の判断に使えず、結局放置される。
    """

    alert: Alert
    #: この行に対して判断ボタンを出してよいか（案件の編集権限）。
    can_decide: bool = False

    @property
    def tone(self) -> str:
        """重要度の色。r=重大 / a=注意 / b=情報。"""

        return {
            Alert.Severity.CRITICAL: "r",
            Alert.Severity.WARNING: "a",
            Alert.Severity.INFO: "b",
        }.get(self.alert.severity, "n")

    @property
    def status_tone(self) -> str:
        """状態の色。未対応だけを赤にし、対象外は目立たせない。"""

        return {
            Alert.Status.OPEN: "r",
            Alert.Status.ACKNOWLEDGED: "a",
            Alert.Status.RESOLVED: "g",
            Alert.Status.DISMISSED: "n",
        }.get(self.alert.status, "n")

    @property
    def evidence(self) -> dict[str, Any]:
        evidence = self.alert.evidence

        return evidence if isinstance(evidence, dict) else {}

    @property
    def reason(self) -> str:
        """検知理由の本文。検知器が必ず入れる `reason` を使う。"""

        return str(self.evidence.get("reason", ""))

    @property
    def observed_items(self) -> list[tuple[str, str]]:
        """実測値（何を観測したか）。"""

        return _evidence_items(self.evidence.get("observed"))

    @property
    def threshold_items(self) -> list[tuple[str, str]]:
        """しきい値（どこを超えたら検知するか）。"""

        return _evidence_items(self.evidence.get("threshold"))

    @property
    def has_evidence(self) -> bool:
        return bool(self.reason or self.observed_items or self.threshold_items)

    @property
    def can_acknowledge(self) -> bool:
        return self.alert.status in SOURCE_STATUSES[Alert.Status.ACKNOWLEDGED]

    @property
    def can_resolve(self) -> bool:
        return self.alert.status in SOURCE_STATUSES[Alert.Status.RESOLVED]

    @property
    def can_dismiss(self) -> bool:
        return self.alert.status in SOURCE_STATUSES[Alert.Status.DISMISSED]

    @property
    def is_closed(self) -> bool:
        """これ以上の判断ができない（確定済みの）アラートか。"""

        return not (self.can_acknowledge or self.can_resolve or self.can_dismiss)

    @property
    def lead_time_days(self) -> int | None:
        return self.alert.lead_time_days


@dataclass(frozen=True)
class AlertBoard:
    """アラート一覧画面が必要とするものすべて。"""

    rows: tuple[AlertRow, ...]
    filters: AlertFilters
    total: int = 0
    open_count: int = 0
    acknowledged_count: int = 0
    resolved_count: int = 0
    dismissed_count: int = 0
    critical_open_count: int = 0
    #: 検知から確認までの平均日数。PoC 受入条件「予兆検知の先行日数」の実測値。
    average_lead_days: float | None = None
    #: 先行日数を算出できたアラート数。母数が分からないと平均を信用できない。
    measured_lead_count: int = 0

    @property
    def has_unmeasured_lead(self) -> bool:
        """先行日数がまだ 1 件も測れていないか。"""

        return self.measured_lead_count == 0


def build_alert_board(
    alerts: QuerySet[Alert],
    filters: AlertFilters,
    display_alerts: Iterable[Alert] | None = None,
    can_decide: Callable[[Alert], bool] | None = None,
) -> AlertBoard:
    """絞り込み済みのアラートから画面表示用の構造を作る。

    `alerts` は集計用の全件、`display_alerts` は表示する 1 ページ分。
    集計を表示行から数えると、ページを送るたびに「未対応 12 件」が
    「未対応 3 件」に変わってしまう。件数は必ず絞り込み後の全件から取る。
    """

    visible = alerts if display_alerts is None else display_alerts
    allowed = can_decide or (lambda alert: False)
    rows = tuple(AlertRow(alert=alert, can_decide=allowed(alert)) for alert in visible)
    counts = alerts.aggregate(
        total=Count("pk"),
        open_count=Count("pk", filter=Q(status=Alert.Status.OPEN)),
        acknowledged_count=Count("pk", filter=Q(status=Alert.Status.ACKNOWLEDGED)),
        resolved_count=Count("pk", filter=Q(status=Alert.Status.RESOLVED)),
        dismissed_count=Count("pk", filter=Q(status=Alert.Status.DISMISSED)),
        critical_open_count=Count(
            "pk", filter=Q(status=Alert.Status.OPEN, severity=Alert.Severity.CRITICAL)
        ),
    )
    average, measured = _lead_time_summary(alerts)

    return AlertBoard(
        rows=rows,
        filters=filters,
        average_lead_days=average,
        measured_lead_count=measured,
        **counts,
    )


def _lead_time_summary(alerts: QuerySet[Alert]) -> tuple[float | None, int]:
    """先行日数の平均と母数。

    差分の平均は DB 側の日時演算がバックエンドごとに違う（SQLite では
    DurationField の平均が取れない）ため、確認済みのものだけを取り出して
    Python 側で数える。対象は `acknowledged_at` が入っているものに限られる。
    """

    pairs = alerts.filter(acknowledged_at__isnull=False).values_list(
        "detected_at", "acknowledged_at"
    )
    days = [(acknowledged - detected).days for detected, acknowledged in pairs]

    if not days:
        return None, 0

    return round(sum(days) / len(days), 1), len(days)


def _evidence_items(value: Any) -> list[tuple[str, str]]:
    """根拠の dict を「日本語ラベル, 値」の並びへ直す。

    値が入れ子（リスト・辞書）のときは要素数だけを出す。画面へ生の JSON を
    貼ると読む気を失わせるだけで、根拠として機能しない。
    """

    if not isinstance(value, dict):
        return []

    items: list[tuple[str, str]] = []

    for key, raw in value.items():
        items.append((EVIDENCE_LABELS.get(key, key), _format_value(raw)))

    return items


def _format_value(raw: Any) -> str:
    if isinstance(raw, bool):
        return "はい" if raw else "いいえ"

    if isinstance(raw, list | tuple):
        return f"{len(raw)}件"

    if isinstance(raw, dict):
        return f"{len(raw)}項目"

    if raw is None:
        return "—"

    return str(raw)


# --- 書き込み ---------------------------------------------------------------


def is_pending(alert: Alert) -> bool:
    """まだ人の判断が 1 度も入っていないか。"""

    return alert.status == Alert.Status.OPEN


def decide_alert(
    alert: Alert,
    *,
    user,
    status: str,
    note: str = "",
    now: datetime | None = None,
) -> Alert:
    """アラートの状態を確定し、確認日時と操作ログを残す。

    `acknowledged_at` は「人が最初に気づいた時刻」なので、いったん入ったら
    上書きしない。解消・対象外へ直接進んだ場合もここで必ず入れる
    （入れないと `lead_time_days` が None のままで、先行日数を実測できない）。

    返すのは保存後の新しいインスタンス。引数の `alert` は変更しない。
    """

    if status not in DECIDABLE_STATUSES:
        raise InvalidDecisionError(f"判断として使えない状態です: {status}")

    reason = (note or "").strip()

    if status in REASON_REQUIRED_STATUSES and not reason:
        raise InvalidDecisionError("対象外にするときは理由を必ず残してください。")

    sources = SOURCE_STATUSES[status]

    if alert.status not in sources:
        raise AlreadyDecidedError("このアラートはすでに確定済みです。")

    values: dict[str, Any] = {"status": status}

    if alert.acknowledged_at is None:
        values["acknowledged_at"] = now or timezone.now()

    # 元の状態を条件に含めることで、同時に 2 人が押しても先勝ちになる。
    updated = Alert.objects.filter(pk=alert.pk, status__in=sources).update(**values)

    if not updated:
        raise AlreadyDecidedError("このアラートはすでに確定済みです。")

    decided = Alert.objects.select_related("project", "project__tenant").get(pk=alert.pk)

    _log(decided, user=user, reason=reason)

    return decided


def _log(alert: Alert, *, user, reason: str) -> None:
    """誰がいつ何を判断したかを操作ログへ残す。監査画面から追えるようにする。

    先行日数も本文に残す。アラート側は最新の状態しか持たないため、
    「そのとき何日遅れて気づいたか」はログを見ないと再現できない。
    """

    lead = alert.lead_time_days
    parts = [reason] if reason else []

    if lead is not None:
        parts.append(f"検知から確認まで {lead}日")

    OperationLog.objects.create(
        tenant=alert.project.tenant,
        user=user if getattr(user, "is_authenticated", False) else None,
        project=alert.project,
        action=DECIDE_ACTION,
        target=f"{alert.get_status_display()} / {alert.title}",
        succeeded=True,
        detail=" / ".join(parts),
    )
