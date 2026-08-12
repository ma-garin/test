"""Policy & Approval Authority / Execution Broker の開発用fake実装。

docs/安全施策.md SC-05（Execution Broker）・SC-06（承認と実行の暗号的束縛）の
データ構造をそのまま持つが、本番運用はしない：

- 署名鍵はKMS/HSMではなく環境変数（開発用デフォルト値あり）。
- 外部システムへは一切接続しない。fake connector が返すのはダミー結果のみ。
- 別プロセス・別workload identityへの分離も行わない（同じDjangoプロセス内）。

安全施策.md 11章が「Authority/Broker/外部監査正本をどの基盤で提供するか」を
人の決定事項としている間は、この fake 実装と Shadow 運用の範囲に留める。
本番化にはこのモジュールの前提を全て置き換える必要がある。
"""

from __future__ import annotations

import datetime
import uuid

from django.db import models

from apps.core.models import TimeStampedModel


class CapabilityStatus(models.TextChoices):
    ISSUED = "issued", "発行済み"
    CONSUMED = "consumed", "実行済み"
    EXPIRED = "expired", "失効"
    REVOKED = "revoked", "取消"


class ExecutionCapability(TimeStampedModel):
    """安全施策.md SC-06 の capability ペイロードそのもの。"""

    capability_id = models.UUIDField("capability ID", default=uuid.uuid4, unique=True, editable=False)
    tenant = models.ForeignKey(
        "accounts.Tenant", verbose_name="テナント", on_delete=models.CASCADE, related_name="+"
    )
    project = models.ForeignKey(
        "projects.Project", verbose_name="案件", on_delete=models.CASCADE, related_name="+"
    )
    # apps.pmo_automation.PmoWorkItem への疎結合参照。FKにせず値だけ持つ
    # （Authority/Brokerは将来別プロセスへ分離される前提のため、
    #  同一DBのFK制約に依存しない設計にする）。
    work_item_id = models.UUIDField("対象Work Item ID")
    plan_version = models.PositiveIntegerField("対象plan版")
    policy_bundle_sha256 = models.CharField("policy bundleハッシュ", max_length=64)
    requested_action = models.CharField("要求操作", max_length=200)
    resource_id = models.CharField("対象リソースID", max_length=200)
    payload_sha256 = models.CharField("実行内容ハッシュ", max_length=64)
    evidence_bundle_sha256 = models.CharField("根拠バンドルハッシュ", max_length=64)
    approved_by_subject_id = models.CharField("承認者subject_id", max_length=200)
    required_role = models.CharField("必要ロール", max_length=64, blank=True)
    issued_at = models.DateTimeField("発行日時")
    expires_at = models.DateTimeField("失効日時")
    nonce = models.CharField("nonce", max_length=64, unique=True)
    idempotency_key = models.CharField("冪等性キー", max_length=200)
    signature = models.TextField("署名（HMAC-SHA256, hex）")
    status = models.CharField(
        "状態", max_length=16, choices=CapabilityStatus.choices, default=CapabilityStatus.ISSUED
    )

    class Meta:
        verbose_name = "実行capability"
        verbose_name_plural = "実行capability"
        ordering = ["-issued_at"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["work_item_id"]),
        ]
        constraints = [
            # 安全施策.md SC-06: capabilityの最大有効期限は10分。
            # issue_capability() の入口チェックだけだと
            # ExecutionCapability.objects.create() を直接呼ぶ経路で
            # バイパスできてしまう（セキュリティレビュー指摘）ため、
            # モデル層のCHECK制約でも強制する。
            models.CheckConstraint(
                condition=models.Q(expires_at__lte=models.F("issued_at") + datetime.timedelta(seconds=600)),
                name="execution_capability_ttl_max_600s",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.requested_action} ({self.status})"


class AuditEvent(TimeStampedModel):
    """外部の改ざん検知付き監査ストアを模した hash chain 記録（開発用）。

    本番では安全施策.md SC-07 の通り、通常アプリDB権限では
    update/delete できない外部ストアへ送る必要があるが、
    この fake 実装では同一DB内のテーブルに留める。
    """

    event_id = models.UUIDField("イベントID", default=uuid.uuid4, unique=True, editable=False)
    correlation_id = models.UUIDField("相関ID")
    previous_hash = models.CharField("直前のevent_hash", max_length=64, blank=True)
    event_hash = models.CharField("このイベントのhash", max_length=64)
    subject = models.CharField("実行主体", max_length=200)
    event_type = models.CharField("イベント種別", max_length=64)
    result = models.CharField("結果", max_length=32)
    detail = models.JSONField(
        "詳細",
        default=dict,
        blank=True,
        help_text="秘密値・認証情報・生本文は入れない。安全な要約のみ。",
    )

    class Meta:
        verbose_name = "監査イベント"
        verbose_name_plural = "監査イベント"
        ordering = ["created_at"]
        indexes = [models.Index(fields=["correlation_id"])]

    def __str__(self) -> str:
        return f"{self.event_type}: {self.result}"
