"""hash chain付きの監査記録（開発用fake実装）。

各イベントは直前イベントの event_hash を previous_hash として持ち、
チェーンが連続していることで改ざんを検知できる形にする。
本番では外部の append-only ストアが正本になる（安全施策.md SC-07）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID

from apps.pmo_authority.models import AuditEvent


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)


def _latest_event_hash(correlation_id: UUID) -> str:
    latest = (
        AuditEvent.objects.filter(correlation_id=correlation_id).order_by("-created_at", "-id").first()
    )
    return latest.event_hash if latest else ""


def record_event(
    *,
    correlation_id: UUID,
    subject: str,
    event_type: str,
    result: str,
    detail: dict,
    now: datetime,
) -> AuditEvent:
    """1件の監査イベントを hash chain に連ねて記録する。"""

    previous_hash = _latest_event_hash(correlation_id)
    # "now" はhash対象に含めない: record_event呼び出し時に渡された now(引数)と
    # DBのcreated_at(auto_now_add)は、DB書き込みのタイミング差でわずかに
    # ズレうる。verify_chain() 側は created_at からしか値を再現できないため、
    # nowをhashに混ぜると正当なイベントでも検証が常に失敗してしまう
    # （セキュリティレビュー指摘: hash chain検証機能の実装時に発覚）。
    payload = {
        "correlation_id": str(correlation_id),
        "subject": subject,
        "event_type": event_type,
        "result": result,
        "detail": detail,
        "previous_hash": previous_hash,
    }
    event_hash = hashlib.sha256((previous_hash + _canonical_json(payload)).encode("utf-8")).hexdigest()

    return AuditEvent.objects.create(
        correlation_id=correlation_id,
        previous_hash=previous_hash,
        event_hash=event_hash,
        subject=subject,
        event_type=event_type,
        result=result,
        detail=detail,
    )


class ChainIntegrityError(ValueError):
    """hash chain の連続性が崩れている（改ざん・欠落の疑い）ことを表す。"""


def verify_chain(correlation_id: UUID) -> int:
    """記録するだけでなく検証できるようにする（セキュリティレビュー指摘対応）。

    record_event() が previous_hash/event_hash を積むだけで、それを後から
    検証する手段が無いと改ざん・削除を事後に検知できない。ここでは
    correlation_id 単位でイベントを時系列に読み、各行の previous_hash が
    直前の event_hash と一致し、かつ event_hash 自体が payload から
    再計算した値と一致することを検証する。

    不整合を見つけたら ChainIntegrityError を送出する。正常なら検証した
    件数を返す。
    """

    events = list(AuditEvent.objects.filter(correlation_id=correlation_id).order_by("created_at", "id"))
    previous_hash = ""
    for event in events:
        if event.previous_hash != previous_hash:
            raise ChainIntegrityError(
                f"event_id={event.event_id}: previous_hash が直前のevent_hashと一致しません"
                "（改ざんまたは欠落の疑い）。"
            )

        payload = {
            "correlation_id": str(event.correlation_id),
            "subject": event.subject,
            "event_type": event.event_type,
            "result": event.result,
            "detail": event.detail,
            "previous_hash": previous_hash,
        }
        expected_hash = hashlib.sha256((previous_hash + _canonical_json(payload)).encode("utf-8")).hexdigest()
        if expected_hash != event.event_hash:
            raise ChainIntegrityError(f"event_id={event.event_id}: event_hash が payload と一致しません（改ざんの疑い）。")

        previous_hash = event.event_hash

    return len(events)
