"""PMO 支援。相談・計画ドラフト・成果物・承認（HITL）。

旧実装の PMO コーチ、プロンプトライブラリ、計画策定エージェント、成果物・承認の
セッション状態を、永続化されたモデルとして持ち直したもの。

承認フローは「セッション上の状態」ではなくテーブルにする。誰がいつ何を承認したかを
後から追跡できることが、PoC の受け入れ条件（HITL 承認、承認前ブロック）の前提になる。
"""

from __future__ import annotations

from django.db import models

from apps.core.models import SoftDeleteModel, TimeStampedModel


class PromptTemplate(SoftDeleteModel):
    """プロンプトライブラリ。"""

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="prompt_templates",
    )
    key = models.SlugField("キー", max_length=64)
    title = models.CharField("名称", max_length=200)
    category = models.CharField("カテゴリ", max_length=64, blank=True)
    description = models.TextField("説明", blank=True)
    body = models.TextField("プロンプト本文")
    intent = models.CharField(
        "対応する意図",
        max_length=32,
        blank=True,
        help_text="apps.agents.models.Intent の値。空なら汎用。",
    )
    is_active = models.BooleanField("有効", default=True)

    class Meta:
        verbose_name = "プロンプトテンプレート"
        verbose_name_plural = "プロンプトテンプレート"
        ordering = ["category", "title"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "key"], name="uniq_prompt_key_per_tenant"),
        ]

    def __str__(self) -> str:
        return self.title


class Consultation(TimeStampedModel):
    """PMO 相談 1 件。オーケストレーターの実行と 1 対 1 で紐づく。"""

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="consultations",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consultations",
    )
    user = models.ForeignKey(
        "accounts.User",
        verbose_name="相談者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consultations",
    )
    question = models.TextField("相談内容")
    prompt_template = models.ForeignKey(
        PromptTemplate,
        verbose_name="使用テンプレート",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consultations",
    )
    agent_run = models.OneToOneField(
        "agents.AgentRun",
        verbose_name="Agentic実行",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consultation",
    )

    class Meta:
        verbose_name = "PMO相談"
        verbose_name_plural = "PMO相談"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.question[:60]


class PlanDraft(TimeStampedModel):
    """計画策定エージェントが作る計画ドラフト。"""

    class Status(models.TextChoices):
        DRAFT = "draft", "下書き"
        REVIEWING = "reviewing", "レビュー中"
        FINALIZED = "finalized", "確定"

    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="plan_drafts",
    )
    title = models.CharField("計画名", max_length=200)
    status = models.CharField("状態", max_length=16, choices=Status.choices, default=Status.DRAFT)
    body = models.TextField("計画本文", blank=True)
    review_points = models.JSONField("レビュー観点", default=list, blank=True)
    agent_run = models.ForeignKey(
        "agents.AgentRun",
        verbose_name="生成元の実行",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="plan_drafts",
    )

    class Meta:
        verbose_name = "計画ドラフト"
        verbose_name_plural = "計画ドラフト"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class Deliverable(SoftDeleteModel):
    """報告書・議事録などの成果物。

    AI が生成した本文（`ai_generated_body`）と、人が編集した本文（`body`）を
    別カラムで持つ。赤字率（人がどれだけ直したか）を測るために必要。
    """

    class Kind(models.TextChoices):
        WEEKLY_REPORT = "weekly_report", "週次報告"
        MONTHLY_REPORT = "monthly_report", "月次報告"
        QUALITY_REPORT = "quality_report", "品質レポート"
        INCIDENT_SUMMARY = "incident_summary", "障害サマリー"
        MEETING_MINUTES = "meeting_minutes", "議事録"
        OTHER = "other", "その他"

    class Status(models.TextChoices):
        DRAFT = "draft", "下書き"
        PENDING_APPROVAL = "pending_approval", "承認待ち"
        APPROVED = "approved", "承認済み"
        REJECTED = "rejected", "差し戻し"

    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="deliverables",
    )
    kind = models.CharField("種別", max_length=32, choices=Kind.choices, default=Kind.OTHER)
    title = models.CharField("タイトル", max_length=200)
    version = models.PositiveIntegerField(
        "版",
        default=1,
        help_text="差し戻し後に作り直すたびに繰り上げる。承認済みの版は本文を変更しない。",
    )
    status = models.CharField("状態", max_length=32, choices=Status.choices, default=Status.DRAFT)
    ai_generated_body = models.TextField("AI生成本文", blank=True)
    body = models.TextField("確定本文", blank=True)
    agent_run = models.ForeignKey(
        "agents.AgentRun",
        verbose_name="生成元の実行",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deliverables",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        verbose_name="作成者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deliverables",
    )

    class Meta:
        verbose_name = "成果物"
        verbose_name_plural = "成果物"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title} v{self.version}"

    @property
    def correction_rate(self) -> float | None:
        """赤字率。AI 生成本文が、人の編集でどれだけ書き換わったか。

        PoC の受け入れ条件「赤字率 20% 未満」の実測値。文字単位の差分比で算出する。
        """

        if not self.ai_generated_body:
            return None

        import difflib

        matcher = difflib.SequenceMatcher(None, self.ai_generated_body, self.body or "")

        return round(1.0 - matcher.ratio(), 4)

    @property
    def can_request_approval(self) -> bool:
        """承認申請してよい状態か。

        根拠不足と評価された生成物は承認へ回さない。PoC 要件の
        「ハルシネーション時の承認前ブロック」に対応する。
        """

        if self.agent_run is None:
            return True

        evidence = getattr(self.agent_run, "evidence", None)

        return evidence is None or not evidence.blocks_approval


class Approval(TimeStampedModel):
    """承認の 1 アクション。差し戻しも 1 レコードとして残す。"""

    class Decision(models.TextChoices):
        REQUESTED = "requested", "承認依頼"
        APPROVED = "approved", "承認"
        REJECTED = "rejected", "差し戻し"

    deliverable = models.ForeignKey(
        Deliverable,
        verbose_name="成果物",
        on_delete=models.CASCADE,
        related_name="approvals",
    )
    actor = models.ForeignKey(
        "accounts.User",
        verbose_name="実施者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approvals",
    )
    decision = models.CharField("判断", max_length=16, choices=Decision.choices)
    comment = models.TextField("コメント", blank=True)

    class Meta:
        verbose_name = "承認履歴"
        verbose_name_plural = "承認履歴"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.deliverable.title} / {self.get_decision_display()}"
