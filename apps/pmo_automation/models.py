"""PMO 自律運用（PMO Work Item）の中核モデル。

既存の `Alert` / `InterventionProposal` / `AgentRun` / `Deliverable` を
置き換えず、「いつ・何を・どの根拠で・どこまで自動で進め・誰に確認させるか」を
一つの仕事として管理する層をここに追加する。

状態機械・自動化レベル・失敗分類の語彙は
`docs/agent/pmo_autopilot_contract.json` を正とし、この契約と一対一で
対応させる。値を追加・変更する場合は契約側も同時に更新すること。
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel
from apps.projects.models import Priority


class WorkKind(models.TextChoices):
    DETECTION_TRIAGE = "detection_triage", "予兆トリアージ"
    DATA_QUALITY_REPAIR = "data_quality_repair", "データ品質修復"
    FORECAST_REVIEW = "forecast_review", "予測レビュー"
    REPORT_CYCLE = "report_cycle", "定例報告サイクル"
    APPROVAL_FOLLOWUP = "approval_followup", "承認フォローアップ"
    INTEGRATION_RECOVERY = "integration_recovery", "連携復旧"
    KNOWLEDGE_QUALITY = "knowledge_quality", "ナレッジ品質"


class RiskLevel(models.TextChoices):
    LOW = "low", "低"
    MEDIUM = "medium", "中"
    HIGH = "high", "高"


class WorkItemState(models.TextChoices):
    """`pmo_autopilot_contract.json` の `states` と一対一で対応する。"""

    NEW = "new", "新規"
    ASSESSING = "assessing", "評価中"
    PLANNED = "planned", "計画済み"
    AUTO_RUNNING = "auto_running", "自動実行中"
    AWAITING_CONFIRMATION = "awaiting_confirmation", "確認待ち"
    AWAITING_APPROVAL = "awaiting_approval", "承認待ち"
    EXECUTING = "executing", "実行中"
    RETRY_SCHEDULED = "retry_scheduled", "再試行予約"
    COMPLETED = "completed", "完了"
    CANCELLED = "cancelled", "取消"
    HOLD = "hold", "保留"
    FAILED = "failed", "失敗"


class AutomationLevel(models.TextChoices):
    """`pmo_autopilot_contract.json` の `automation_levels` と一対一で対応する。"""

    OBSERVE = "observe", "観測"
    INTERNAL_APPLY = "internal_apply", "内部反映"
    CONFIRM = "confirm", "確認待ち"
    APPROVE = "approve", "承認待ち"
    PROHIBITED = "prohibited", "禁止"


class FailureCategory(models.TextChoices):
    """`pmo_autopilot_contract.json` の `failure_policy` カテゴリと一対一で対応する。"""

    CREDENTIAL = "credential", "資格情報"
    PERMISSION = "permission", "権限"
    POLICY = "policy", "ポリシー"
    SECRETS = "secrets", "秘密情報"
    TRANSIENT = "transient", "一時障害"
    TIMEOUT = "timeout", "タイムアウト"
    UNKNOWN = "unknown", "不明"
    TEST = "test", "テスト"


class PmoWorkItem(TimeStampedModel):
    """PMOの「今、誰が、何を待っているか」を束ねる単位。

    tenant・project は子モデル（WorkPlan 等）から work_item 経由でも辿れるが、
    テナント越境検証をレコード単体で完結させるため、安全施策.md SC-09 に
    倣ってここへも直接持たせる（意図的な非正規化）。
    """

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="pmo_work_items",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="pmo_work_items",
    )
    kind = models.CharField("種別", max_length=32, choices=WorkKind.choices)
    state = models.CharField(
        "状態", max_length=32, choices=WorkItemState.choices, default=WorkItemState.NEW
    )
    priority = models.CharField(
        "優先度", max_length=16, choices=Priority.choices, default=Priority.MEDIUM
    )
    risk_level = models.CharField(
        "リスク", max_length=16, choices=RiskLevel.choices, default=RiskLevel.MEDIUM
    )
    source_type = models.CharField(
        "発生源種別",
        max_length=64,
        help_text="alert / integration_job / forecast / schedule など。",
    )
    source_key = models.CharField("発生源キー", max_length=200, blank=True)
    dedupe_key = models.CharField(
        "重複判定キー",
        max_length=200,
        help_text="source_type + source_key + policy_version から決定的に導く。",
    )
    owner = models.CharField("担当", max_length=120, blank=True)
    due_at = models.DateTimeField("期限", null=True, blank=True)
    policy_snapshot = models.JSONField(
        "評価時点のポリシー",
        default=dict,
        blank=True,
        help_text="assessing→planned遷移時に評価した policy 結果を固定する。",
    )
    correlation_id = models.UUIDField(
        "相関ID", default=uuid.uuid4, editable=False, db_index=True
    )
    block_reason = models.TextField("保留・確認待ちの理由", blank=True)
    is_active = models.BooleanField(
        "有効",
        default=True,
        help_text="dedupe 対象。terminal state（completed/cancelled/hold）到達で False にする。",
    )

    class Meta:
        verbose_name = "PMO自律作業"
        verbose_name_plural = "PMO自律作業"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "dedupe_key"],
                condition=Q(is_active=True),
                name="uniq_active_pmoworkitem_per_tenant_dedupe",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "project", "state"]),
            models.Index(fields=["tenant", "kind", "state"]),
            models.Index(fields=["correlation_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} / {self.dedupe_key}"

    def save(self, *args, **kwargs):
        """project は必ず自分と同じ tenant に属する（SC-09: クロステーブルの
        テナント整合はDBのCHECK制約で表現できないため、ここで強制する）。
        """

        if self.project_id and self.tenant_id and self.project.tenant_id != self.tenant_id:
            raise ValueError(
                "PmoWorkItem: project のテナントと tenant が一致しません "
                f"(tenant={self.tenant_id}, project.tenant={self.project.tenant_id})."
            )

        super().save(*args, **kwargs)


class WorkPlanState(models.TextChoices):
    DRAFT = "draft", "下書き"
    ACTIVE = "active", "有効"
    SUPERSEDED = "superseded", "失効（新版に置換）"


class WorkPlan(TimeStampedModel):
    """Plan は追記型。承認後に内容を変えたら新しい版を作る。"""

    work_item = models.ForeignKey(
        PmoWorkItem,
        verbose_name="対象Work Item",
        on_delete=models.CASCADE,
        related_name="plans",
    )
    version = models.PositiveIntegerField("版")
    summary = models.TextField("計画概要", blank=True)
    automation_level = models.CharField(
        "自動化レベル", max_length=16, choices=AutomationLevel.choices
    )
    state = models.CharField(
        "状態", max_length=16, choices=WorkPlanState.choices, default=WorkPlanState.DRAFT
    )
    created_from_run = models.ForeignKey(
        "agents.AgentRun",
        verbose_name="生成元の実行",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pmo_work_plans",
    )

    class Meta:
        verbose_name = "作業計画"
        verbose_name_plural = "作業計画"
        ordering = ["work_item", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["work_item", "version"], name="uniq_workplan_version"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.work_item_id} v{self.version}"


class WorkStepState(models.TextChoices):
    PENDING = "pending", "未処理"
    RUNNING = "running", "実行中"
    SUCCEEDED = "succeeded", "成功"
    FAILED = "failed", "失敗"
    SKIPPED = "skipped", "スキップ"
    RETRY_SCHEDULED = "retry_scheduled", "再試行予約"
    HOLD = "hold", "保留"


class WorkStep(TimeStampedModel):
    plan = models.ForeignKey(
        WorkPlan, verbose_name="所属計画", on_delete=models.CASCADE, related_name="steps"
    )
    order = models.PositiveIntegerField("順序")
    kind = models.CharField(
        "種別",
        max_length=64,
        help_text="internal_draft / recalculation / external_notify など。",
    )
    automation_level = models.CharField(
        "自動化レベル", max_length=16, choices=AutomationLevel.choices
    )
    input_snapshot = models.JSONField("入力スナップショット", default=dict, blank=True)
    idempotency_key = models.CharField("冪等性キー", max_length=200)
    state = models.CharField(
        "状態", max_length=16, choices=WorkStepState.choices, default=WorkStepState.PENDING
    )
    attempt_count = models.PositiveIntegerField("試行回数", default=0)
    next_retry_at = models.DateTimeField("次回再試行予定", null=True, blank=True)
    result_summary = models.TextField("結果要約", blank=True)

    class Meta:
        verbose_name = "作業ステップ"
        verbose_name_plural = "作業ステップ"
        ordering = ["plan", "order"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "order"], name="uniq_workstep_order"),
            models.UniqueConstraint(
                fields=["plan", "idempotency_key"], name="uniq_workstep_idempotency_key"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.plan_id} #{self.order} {self.kind}"


class EvidenceBundle(TimeStampedModel):
    """承認・外部反映の直前に鮮度とスコープを再評価する根拠の単位。"""

    work_item = models.ForeignKey(
        PmoWorkItem,
        verbose_name="対象Work Item",
        on_delete=models.CASCADE,
        related_name="evidence_bundles",
    )
    source_type = models.CharField("出所種別", max_length=64)
    source_ref = models.CharField(
        "出所参照",
        max_length=500,
        help_text="元レコードのURLまたはUUID。表示用文字列を主キーにしない。",
    )
    captured_at = models.DateTimeField("取得日時")
    expires_at = models.DateTimeField("失効日時", null=True, blank=True)
    content_hash = models.CharField("内容ハッシュ", max_length=128)
    scope = models.JSONField(
        "適用範囲", default=dict, blank=True, help_text="テナント・案件・対象を明示する。"
    )
    confidence = models.FloatField("信頼度", null=True, blank=True)
    conflict_group = models.CharField(
        "競合グループ",
        max_length=200,
        blank=True,
        help_text="同じ主張に対する肯定・否定根拠を束ねる。",
    )
    agent_run = models.ForeignKey(
        "agents.AgentRun",
        verbose_name="生成元の実行",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pmo_evidence_bundles",
    )

    class Meta:
        verbose_name = "根拠バンドル"
        verbose_name_plural = "根拠バンドル"
        ordering = ["-captured_at"]
        indexes = [models.Index(fields=["work_item", "conflict_group"])]

    def __str__(self) -> str:
        return f"{self.work_item_id} / {self.source_ref}"


class AutomationPolicy(TimeStampedModel):
    """最も厳しい一致規則を採用する評価対象ポリシー。

    評価結果そのものは Work Item 側へスナップショットで保存し、
    このレコードは「評価に使う規則」だけを持つ。
    """

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="pmo_automation_policies",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件（未指定はテナント既定値）",
        on_delete=models.CASCADE,
        related_name="pmo_automation_policies",
        null=True,
        blank=True,
    )
    work_kind = models.CharField("対象種別", max_length=32, choices=WorkKind.choices)
    risk_level = models.CharField("対象リスク", max_length=16, choices=RiskLevel.choices)
    automation_level = models.CharField(
        "許可する自動化レベル", max_length=16, choices=AutomationLevel.choices
    )
    required_role = models.CharField("必要ロール", max_length=32, blank=True)
    max_age_minutes = models.PositiveIntegerField("根拠の最大鮮度(分)", null=True, blank=True)
    version = models.PositiveIntegerField("版", default=1)
    enabled = models.BooleanField("有効", default=True)

    class Meta:
        verbose_name = "自動化ポリシー"
        verbose_name_plural = "自動化ポリシー"
        ordering = ["tenant", "work_kind", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "project", "work_kind", "risk_level", "version"],
                name="uniq_automation_policy_version",
            ),
        ]

    def __str__(self) -> str:
        scope = self.project.code if self.project_id else "テナント既定"
        return f"{scope} / {self.work_kind} / {self.risk_level} v{self.version}"


class ApprovalStatus(models.TextChoices):
    PENDING = "pending", "承認待ち"
    APPROVED = "approved", "承認"
    REJECTED = "rejected", "却下"
    RETURNED = "returned", "差し戻し"
    EXPIRED = "expired", "失効"
    HOLD = "hold", "保留"


class ApprovalRequest(TimeStampedModel):
    """承認は一回だけで上書き不可。根拠・plan版・対象が変われば失効させる。"""

    work_item = models.ForeignKey(
        PmoWorkItem,
        verbose_name="対象Work Item",
        on_delete=models.CASCADE,
        related_name="approval_requests",
    )
    plan_version = models.PositiveIntegerField("対象plan版")
    requested_action = models.CharField("要求操作", max_length=200)
    diff_summary = models.TextField("実行前後の差分", blank=True)
    required_role = models.CharField("必要ロール", max_length=32, blank=True)
    status = models.CharField(
        "状態", max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING
    )
    expires_at = models.DateTimeField("失効日時", null=True, blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        verbose_name="作成者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="職務分掌チェック用。作成者は自己承認できない。",
    )
    last_executed_by = models.ForeignKey(
        "accounts.User",
        verbose_name="最後の実行者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="職務分掌チェック用。最後の実行者は自己承認できない。",
    )
    decided_by = models.ForeignKey(
        "accounts.User",
        verbose_name="承認者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_at = models.DateTimeField("判断日時", null=True, blank=True)
    decision_reason = models.TextField("判断理由", blank=True)

    class Meta:
        verbose_name = "承認依頼"
        verbose_name_plural = "承認依頼"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["work_item", "status"])]

    def __str__(self) -> str:
        return f"{self.work_item_id} / {self.requested_action}"


class ExecutionOutcome(models.TextChoices):
    SUCCEEDED = "succeeded", "成功"
    FAILED = "failed", "失敗"
    SKIPPED = "skipped", "スキップ"


class ExecutionAttempt(TimeStampedModel):
    """失敗も必ず保存する。秘密値・認証値・生本文は保存しない。"""

    step = models.ForeignKey(
        WorkStep, verbose_name="対象Step", on_delete=models.CASCADE, related_name="attempts"
    )
    started_at = models.DateTimeField("開始日時")
    ended_at = models.DateTimeField("終了日時", null=True, blank=True)
    outcome = models.CharField("結果", max_length=16, choices=ExecutionOutcome.choices)
    failure_category = models.CharField(
        "失敗分類", max_length=16, choices=FailureCategory.choices, blank=True
    )
    safe_summary = models.TextField("安全要約", blank=True)
    external_receipt = models.JSONField(
        "外部受領証跡",
        default=dict,
        blank=True,
        help_text="外部ID・結果hash・時刻・connector名のみ。トークンや生本文は入れない。",
    )

    class Meta:
        verbose_name = "実行試行"
        verbose_name_plural = "実行試行"
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["step", "outcome"])]

    def __str__(self) -> str:
        return f"{self.step_id} / {self.outcome}"


class WorkLink(TimeStampedModel):
    """既存モデルとの関係を正規化し、画面・監査を横断可能にする。

    各リンクは1レコードにつき1ターゲットのみを持つ想定である
    （`worklink_at_least_one_target` はデータ不備の検出用）。
    on_delete は CASCADE にする。SET_NULL にすると、単一ターゲットの
    リンクで参照先が削除された際に全FKがNULLになり、
    `worklink_at_least_one_target` に違反して参照先の削除自体が
    失敗してしまうため（レビュー指摘）。
    """

    work_item = models.ForeignKey(
        PmoWorkItem, verbose_name="対象Work Item", on_delete=models.CASCADE, related_name="links"
    )
    alert = models.ForeignKey(
        "dashboard.Alert",
        verbose_name="アラート",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    proposal = models.ForeignKey(
        "dashboard.InterventionProposal",
        verbose_name="AI介入提案",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    agent_run = models.ForeignKey(
        "agents.AgentRun",
        verbose_name="Agent実行",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    deliverable = models.ForeignKey(
        "pmo.Deliverable",
        verbose_name="成果物",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    approval = models.ForeignKey(
        ApprovalRequest,
        verbose_name="承認依頼",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    integration_job = models.ForeignKey(
        "integrations.SyncJob",
        verbose_name="連携ジョブ",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "関連リンク"
        verbose_name_plural = "関連リンク"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(alert__isnull=False)
                    | Q(proposal__isnull=False)
                    | Q(agent_run__isnull=False)
                    | Q(deliverable__isnull=False)
                    | Q(approval__isnull=False)
                    | Q(integration_job__isnull=False)
                ),
                name="worklink_at_least_one_target",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.work_item_id} link"
