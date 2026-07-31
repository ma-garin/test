"""案件と、その配下の PMO 管理対象データ。

旧実装では案件は `07.feedback/projects.json`、WBS・課題・リスク・不具合は
`demo_data.py` / `ai_project_control.py` 内の辞書だった。ここでは、予兆検知や
KPI 集計が SQL で書けるよう、正規化したテーブルとして持つ。
"""

from __future__ import annotations

from django.db import models

from apps.core.models import SoftDeleteModel, TimeStampedModel


class ProjectStatus(models.TextChoices):
    PREPARING = "preparing", "準備中"
    ON_SCHEDULE = "on_schedule", "オンスケ案件"
    ATTENTION = "attention", "注意案件"
    DELAYED = "delayed", "遅延・課題多発案件"
    CLOSED = "closed", "終了"


class RagStatus(models.TextChoices):
    """進捗信号。RAG 検索の RAG とは別概念なので、表示名も分けて扱う。"""

    GREEN = "green", "Green"
    YELLOW = "yellow", "Yellow"
    RED = "red", "Red"
    GRAY = "gray", "Gray"


class Project(SoftDeleteModel):
    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="projects",
    )
    code = models.SlugField("案件コード", max_length=64)
    name = models.CharField("案件名", max_length=200)
    description = models.TextField("概要", blank=True)
    status = models.CharField(
        "ステータス",
        max_length=32,
        choices=ProjectStatus.choices,
        default=ProjectStatus.PREPARING,
    )
    rag_status = models.CharField(
        "RAG信号",
        max_length=16,
        choices=RagStatus.choices,
        default=RagStatus.GRAY,
    )
    progress_percent = models.DecimalField(
        "進捗率",
        max_digits=5,
        decimal_places=2,
        default=0,
    )
    project_manager = models.CharField("PM", max_length=120, blank=True)
    pmo_manager = models.CharField("PMO", max_length=120, blank=True)
    planned_start = models.DateField("計画開始日", null=True, blank=True)
    planned_end = models.DateField("計画終了日", null=True, blank=True)
    is_demo = models.BooleanField(
        "体験用案件",
        default=False,
        help_text="体験用モック案件。実データと混在させないための区別に使う。",
    )

    class Meta:
        verbose_name = "案件"
        verbose_name_plural = "案件"
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uniq_project_code_per_tenant"),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class ProjectMember(TimeStampedModel):
    project = models.ForeignKey(
        Project,
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="members",
    )
    user = models.ForeignKey(
        "accounts.User",
        verbose_name="利用者",
        on_delete=models.CASCADE,
        related_name="project_memberships",
    )
    role_label = models.CharField("案件内の役割", max_length=64, blank=True)

    class Meta:
        verbose_name = "案件メンバー"
        verbose_name_plural = "案件メンバー"
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="uniq_project_member"),
        ]

    def __str__(self) -> str:
        return f"{self.project.code} / {self.user}"


class ProjectScopedModel(TimeStampedModel):
    """案件配下のデータに共通する親子関係。"""

    project = models.ForeignKey(
        Project,
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="%(class)s_set",
    )

    class Meta:
        abstract = True


class Priority(models.TextChoices):
    LOW = "low", "低"
    MEDIUM = "medium", "中"
    HIGH = "high", "高"
    URGENT = "urgent", "最優先"


class WbsTask(ProjectScopedModel):
    """WBS タスク。計画と実績の差分が予兆検知の入力になる。"""

    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "未着手"
        IN_PROGRESS = "in_progress", "進行中"
        BLOCKED = "blocked", "ブロック中"
        DONE = "done", "完了"
        ARCHIVED = "archived", "アーカイブ"

    class FollowUpState(models.TextChoices):
        """PMO のフォロー状態。誰が次に動くべきかを画面上で明示するために持つ。"""

        NONE = "none", "フォロー不要"
        WATCHING = "watching", "経過観察"
        FOLLOWING = "following", "フォロー中"
        ESCALATED = "escalated", "エスカレーション済み"

    parent = models.ForeignKey(
        "self",
        verbose_name="親タスク",
        on_delete=models.CASCADE,
        related_name="children",
        null=True,
        blank=True,
    )
    related_tasks = models.ManyToManyField(
        "self",
        verbose_name="関連タスク",
        blank=True,
        symmetrical=True,
    )
    wbs_code = models.CharField("WBSコード", max_length=64)
    name = models.CharField("タスク名", max_length=300)
    owner = models.CharField("担当", max_length=120, blank=True)
    status = models.CharField("状態", max_length=32, choices=Status.choices, default=Status.NOT_STARTED)
    priority = models.CharField("優先度", max_length=16, choices=Priority.choices, default=Priority.MEDIUM)
    planned_start = models.DateField("計画開始日", null=True, blank=True)
    planned_end = models.DateField("計画終了日", null=True, blank=True)
    actual_start = models.DateField("実績開始日", null=True, blank=True)
    actual_end = models.DateField("実績終了日", null=True, blank=True)
    progress_percent = models.DecimalField("進捗率", max_digits=5, decimal_places=2, default=0)
    # 工数が無いと出来高（EV）を金額・時間で測れず、「いつ終わるか」に答えられない。
    # 進捗率だけでは、残り20%が1日なのか1ヶ月なのか判断できない。
    planned_hours = models.DecimalField(
        "計画工数",
        max_digits=8,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="人時。EVM の PV / EV の算出に使う",
    )
    actual_hours = models.DecimalField(
        "実績工数",
        max_digits=8,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="人時。EVM の AC の算出に使う",
    )
    is_critical_path = models.BooleanField("クリティカルパス", default=False)
    next_action = models.CharField("次アクション", max_length=300, blank=True)
    ball_holder = models.CharField(
        "ボール保持者",
        max_length=120,
        blank=True,
        help_text="次に動く責任者。担当と分けて持つ（顧客待ち・他部署待ちを表現するため）。",
    )
    follow_up_state = models.CharField(
        "PMOフォロー状態",
        max_length=16,
        choices=FollowUpState.choices,
        default=FollowUpState.NONE,
    )
    evidence_note = models.TextField(
        "根拠メモ",
        blank=True,
        help_text="この状態・判断の根拠。AI 生成内容を採用した場合もここへ残す。",
    )

    class Meta:
        verbose_name = "WBSタスク"
        verbose_name_plural = "WBSタスク"
        ordering = ["wbs_code"]
        constraints = [
            models.UniqueConstraint(fields=["project", "wbs_code"], name="uniq_wbs_code_per_project"),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "priority"]),
        ]

    def __str__(self) -> str:
        return f"{self.wbs_code} {self.name}"

    @property
    def is_archived(self) -> bool:
        return self.status == self.Status.ARCHIVED

    @property
    def delay_days(self) -> int | None:
        """計画終了日に対する遅れ日数。未完了なら実績日ではなく本日で測る。"""

        from django.utils import timezone

        if self.planned_end is None:
            return None

        reference = self.actual_end or timezone.localdate()
        delta = (reference - self.planned_end).days

        return delta if delta > 0 else 0


class Milestone(ProjectScopedModel):
    name = models.CharField("マイルストーン名", max_length=200)
    planned_date = models.DateField("計画日")
    forecast_date = models.DateField("見込日", null=True, blank=True)
    actual_date = models.DateField("実績日", null=True, blank=True)
    is_gate = models.BooleanField("品質ゲート", default=False)

    class Meta:
        verbose_name = "マイルストーン"
        verbose_name_plural = "マイルストーン"
        ordering = ["planned_date"]

    def __str__(self) -> str:
        return self.name


class Severity(models.TextChoices):
    LOW = "low", "低"
    MEDIUM = "medium", "中"
    HIGH = "high", "高"
    CRITICAL = "critical", "重大"


class Issue(ProjectScopedModel):
    class Status(models.TextChoices):
        OPEN = "open", "未対応"
        IN_PROGRESS = "in_progress", "対応中"
        BLOCKED = "blocked", "ブロック中"
        RESOLVED = "resolved", "解決"
        CLOSED = "closed", "完了"

    title = models.CharField("課題", max_length=300)
    description = models.TextField("内容", blank=True)
    status = models.CharField("状態", max_length=32, choices=Status.choices, default=Status.OPEN)
    severity = models.CharField("重大度", max_length=16, choices=Severity.choices, default=Severity.MEDIUM)
    owner = models.CharField("担当", max_length=120, blank=True)
    due_date = models.DateField("対応期限", null=True, blank=True)
    resolved_at = models.DateTimeField("解決日時", null=True, blank=True)
    external_key = models.CharField(
        "外部キー",
        max_length=120,
        blank=True,
        help_text="Jira 等の課題キー。外部連携は将来対応で、現時点では参照用の文字列として保持する。",
    )

    class Meta:
        verbose_name = "課題"
        verbose_name_plural = "課題"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "status", "severity"])]

    def __str__(self) -> str:
        return self.title


class Risk(ProjectScopedModel):
    class Status(models.TextChoices):
        IDENTIFIED = "identified", "識別済み"
        MONITORING = "monitoring", "監視中"
        MITIGATING = "mitigating", "対応中"
        MATERIALIZED = "materialized", "顕在化"
        CLOSED = "closed", "クローズ"

    title = models.CharField("リスク", max_length=300)
    description = models.TextField("内容", blank=True)
    status = models.CharField("状態", max_length=32, choices=Status.choices, default=Status.IDENTIFIED)
    probability = models.PositiveSmallIntegerField("発生確率(1-5)", default=3)
    impact = models.PositiveSmallIntegerField("影響度(1-5)", default=3)
    mitigation = models.TextField("対応方針", blank=True)
    owner = models.CharField("担当", max_length=120, blank=True)
    due_date = models.DateField("対応期限", null=True, blank=True)

    class Meta:
        verbose_name = "リスク"
        verbose_name_plural = "リスク"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title

    @property
    def score(self) -> int:
        return self.probability * self.impact


class ChangeRequest(ProjectScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "起票"
        UNDER_REVIEW = "under_review", "影響分析中"
        PENDING_APPROVAL = "pending_approval", "承認待ち"
        APPROVED = "approved", "承認済み"
        REJECTED = "rejected", "却下"

    title = models.CharField("変更要求", max_length=300)
    description = models.TextField("内容", blank=True)
    status = models.CharField("状態", max_length=32, choices=Status.choices, default=Status.DRAFT)
    requested_by = models.CharField("起票者", max_length=120, blank=True)
    impact_summary = models.TextField("影響分析", blank=True)
    impact_scope = models.JSONField(
        "影響範囲",
        default=list,
        blank=True,
        help_text="影響を受ける機能・工程・成果物。関連 WBS は affected_tasks で持つ。",
    )
    affected_tasks = models.ManyToManyField(
        WbsTask,
        verbose_name="影響を受けるタスク",
        blank=True,
        related_name="change_requests",
    )
    estimated_effort_days = models.DecimalField(
        "影響工数(人日)", max_digits=8, decimal_places=2, null=True, blank=True
    )
    schedule_impact_days = models.IntegerField(
        "スケジュール影響(日)",
        null=True,
        blank=True,
        help_text="正なら遅延方向。判断材料として影響工数と分けて持つ。",
    )
    decided_by = models.ForeignKey(
        "accounts.User",
        verbose_name="判断者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="change_decisions",
    )
    decided_at = models.DateTimeField("判断日時", null=True, blank=True)
    decision_reason = models.TextField("判断理由", blank=True)

    class Meta:
        verbose_name = "変更要求"
        verbose_name_plural = "変更要求"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class Defect(ProjectScopedModel):
    class Status(models.TextChoices):
        NEW = "new", "新規"
        ANALYZING = "analyzing", "分析中"
        FIXING = "fixing", "修正中"
        VERIFYING = "verifying", "確認中"
        CLOSED = "closed", "完了"

    title = models.CharField("不具合", max_length=300)
    description = models.TextField("内容", blank=True)
    status = models.CharField("状態", max_length=32, choices=Status.choices, default=Status.NEW)
    severity = models.CharField("重大度", max_length=16, choices=Severity.choices, default=Severity.MEDIUM)
    phase = models.CharField("検出工程", max_length=64, blank=True)
    detected_on = models.DateField("検出日", null=True, blank=True)
    closed_on = models.DateField("完了日", null=True, blank=True)

    class Meta:
        verbose_name = "不具合"
        verbose_name_plural = "不具合"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "status", "severity"])]

    def __str__(self) -> str:
        return self.title


class QualityMetric(ProjectScopedModel):
    """テスト消化率・バグ収束率などの時系列指標。"""

    measured_on = models.DateField("計測日")
    metric_key = models.CharField("指標キー", max_length=64)
    metric_label = models.CharField("指標名", max_length=120, blank=True)
    value = models.DecimalField("値", max_digits=12, decimal_places=4)
    target_value = models.DecimalField("目標値", max_digits=12, decimal_places=4, null=True, blank=True)
    threshold = models.DecimalField(
        "閾値",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="品質ゲートの合否判定に使う値。",
    )
    higher_is_better = models.BooleanField(
        "値は大きいほど良い",
        default=True,
        help_text="消化率なら True、不具合密度なら False。閾値判定の向きを決める。",
    )
    unit = models.CharField("単位", max_length=32, blank=True)

    class Meta:
        verbose_name = "品質指標"
        verbose_name_plural = "品質指標"
        ordering = ["-measured_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "metric_key", "measured_on"],
                name="uniq_quality_metric_per_day",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.metric_label or self.metric_key} @ {self.measured_on}"

    @property
    def passes_gate(self) -> bool | None:
        """品質ゲートを満たしているか。閾値未設定なら判定しない。"""

        if self.threshold is None:
            return None

        return self.value >= self.threshold if self.higher_is_better else self.value <= self.threshold
