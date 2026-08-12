"""AH-05: 外部イベントの受付。

Webhook は再送・順不同・欠落を前提にする。外部の更新を直接 WBS や不具合へ書くと、
「2 回届いた」「順番が逆だった」「外部で訂正された」を後から追えない。
まず受付記録として保存し、正規化・関連付け・再計算はその後に行う。

不変条件:
- `source` + `external_event_id`、それが無ければ内容ハッシュで冪等化する。
- 二重に届いたイベントは `duplicate` として残す。捨てない（欠落調査に使う）。
- 失敗は握りつぶさず `failed` と理由を残す。
"""

from __future__ import annotations

import hashlib
import json

from django.db import models

from apps.projects.models import ProjectScopedModel


class InboundEvent(ProjectScopedModel):
    """外部から届いた 1 イベントの受付記録。"""

    class Status(models.TextChoices):
        RECEIVED = "received", "受付"
        PROCESSED = "processed", "処理済み"
        DUPLICATE = "duplicate", "重複"
        FAILED = "failed", "失敗"
        IGNORED = "ignored", "対象外"

    source = models.CharField("情報源", max_length=32)
    external_event_id = models.CharField("外部イベントID", max_length=200, blank=True)
    event_type = models.CharField("イベント種別", max_length=64)
    payload_hash = models.CharField("内容ハッシュ", max_length=64, db_index=True)
    occurred_at = models.DateTimeField("発生時刻")
    received_at = models.DateTimeField("受付時刻", auto_now_add=True, db_index=True)
    status = models.CharField(
        "状態", max_length=16, choices=Status.choices, default=Status.RECEIVED
    )
    error_reason = models.CharField("失敗理由", max_length=300, blank=True)
    signal = models.ForeignKey(
        "forecast.Signal",
        verbose_name="正規化後のSignal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inbound_events",
    )
    duplicate_of = models.ForeignKey(
        "self",
        verbose_name="重複元",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="duplicates",
    )

    class Meta:
        verbose_name = "受信イベント"
        verbose_name_plural = "受信イベント"
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["project", "source", "-received_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.source}/{self.event_type} {self.get_status_display()}"

    @staticmethod
    def compute_hash(payload: dict) -> str:
        """ペイロードの内容ハッシュ。キー順に依存しないよう整列してから取る。"""

        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def is_duplicate(self) -> bool:
        return self.status == self.Status.DUPLICATE
