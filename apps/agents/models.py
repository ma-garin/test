"""Agentic RAG の実行トレース。

`docs/reference/Agentic-RAG_Spec.plan.req.md` の以下の要件に対応する。

- REQ-AG-002 意図分類
- REQ-AG-003 実行計画生成
- REQ-AG-006 根拠評価
- REQ-AG-008 Trace保存
- REQ-AG-010 人間判断の明示

仕様書 11.1 の Agentic Trace JSON をそのままテーブルへ落としたもの。
JSON 1 本で持たずステップを分けているのは、途中で失敗した実行も
「どこまで進んだか」を残せるようにするため。
"""

from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class Intent(models.TextChoices):
    """旧 `orchestrator.INTENT_LABELS` をそのまま踏襲する。"""

    DELAY = "delay", "進捗遅延"
    RISK = "risk", "リスク相談"
    ISSUE = "issue", "課題整理"
    QUALITY = "quality", "品質懸念"
    CHANGE = "change", "変更影響"
    TEST = "test", "テスト管理"
    GENERAL = "general", "一般PMO相談"


class Recommendation(models.TextChoices):
    """根拠評価の結論（仕様書 11.2）。"""

    ANSWER = "answer", "回答する"
    ANSWER_WITH_CAUTION = "answer_with_caution", "注意付きで回答する"
    ASK_CLARIFICATION = "ask_clarification", "追加確認を求める"


class Level(models.TextChoices):
    LOW = "low", "低"
    MEDIUM = "medium", "中"
    HIGH = "high", "高"


class AgentRun(TimeStampedModel):
    """オーケストレーターの 1 回の実行。"""

    class Area(models.TextChoices):
        RAG_SEARCH = "rag_search", "RAG検索"
        RAG_CHAT = "rag_chat", "RAGチャット"
        PMO_CONSULTATION = "pmo_consultation", "PMO相談"
        PLANNING = "planning", "計画ドラフト"
        DELIVERABLE = "deliverable", "成果物生成"

    class Status(models.TextChoices):
        RUNNING = "running", "実行中"
        SUCCEEDED = "succeeded", "完了"
        FAILED = "failed", "失敗"
        ABORTED = "aborted", "中断"

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="agent_runs",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_runs",
    )
    user = models.ForeignKey(
        "accounts.User",
        verbose_name="実行者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_runs",
    )
    area = models.CharField("実行画面", max_length=32, choices=Area.choices)
    status = models.CharField("状態", max_length=16, choices=Status.choices, default=Status.RUNNING)
    user_input = models.TextField("入力")
    intent = models.CharField("意図", max_length=32, choices=Intent.choices, default=Intent.GENERAL)
    intent_confidence = models.FloatField("意図分類の確信度", default=0.0)
    plan = models.JSONField(
        "実行計画",
        default=dict,
        blank=True,
        help_text="使用ツール、検索クエリ、期待する出力。仕様書 REQ-AG-003。",
    )
    answer = models.ForeignKey(
        "rag.RagAnswer",
        verbose_name="生成回答",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_runs",
    )
    loop_count = models.PositiveSmallIntegerField(
        "ループ回数",
        default=0,
        help_text="NFR-AG-002: settings.AGENT['MAX_LOOPS'] を超えないこと。",
    )
    elapsed_ms = models.PositiveIntegerField("所要時間(ms)", default=0)
    error_message = models.TextField("エラー", blank=True)

    class Meta:
        verbose_name = "Agentic実行"
        verbose_name_plural = "Agentic実行"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "area", "status"])]

    def __str__(self) -> str:
        return f"{self.get_area_display()} / {self.get_intent_display()}"


class AgentStep(TimeStampedModel):
    """実行計画の 1 ステップ。推論プロセス開示（REQ-AG-009）の表示元。"""

    class Status(models.TextChoices):
        OK = "ok", "成功"
        SKIPPED = "skipped", "スキップ"
        FAILED = "failed", "失敗"

    run = models.ForeignKey(
        AgentRun,
        verbose_name="実行",
        on_delete=models.CASCADE,
        related_name="steps",
    )
    order = models.PositiveSmallIntegerField("順序")
    tool_name = models.CharField("ツール名", max_length=64)
    status = models.CharField("状態", max_length=16, choices=Status.choices, default=Status.OK)
    input_summary = models.TextField("入力概要", blank=True)
    output_summary = models.TextField("出力概要", blank=True)
    elapsed_ms = models.PositiveIntegerField("所要時間(ms)", default=0)

    class Meta:
        verbose_name = "実行ステップ"
        verbose_name_plural = "実行ステップ"
        ordering = ["run", "order"]
        constraints = [
            models.UniqueConstraint(fields=["run", "order"], name="uniq_step_order_per_run"),
        ]

    def __str__(self) -> str:
        return f"{self.order}. {self.tool_name}"


class EvidenceEvaluation(TimeStampedModel):
    """根拠の十分性評価（仕様書 11.2）。

    確信度が低いまま成果物を確定させないための、承認前ゲートの判断材料になる。
    """

    run = models.OneToOneField(
        AgentRun,
        verbose_name="実行",
        on_delete=models.CASCADE,
        related_name="evidence",
    )
    confidence = models.FloatField("確信度", default=0.0)
    relevance = models.CharField("関連度", max_length=16, choices=Level.choices, default=Level.LOW)
    coverage = models.CharField("網羅度", max_length=16, choices=Level.choices, default=Level.LOW)
    has_conflict = models.BooleanField("根拠間の矛盾", default=False)
    missing_information = models.JSONField("不足情報", default=list, blank=True)
    recommendation = models.CharField(
        "推奨",
        max_length=32,
        choices=Recommendation.choices,
        default=Recommendation.ASK_CLARIFICATION,
    )
    notes = models.TextField("備考", blank=True)

    class Meta:
        verbose_name = "根拠評価"
        verbose_name_plural = "根拠評価"

    def __str__(self) -> str:
        return f"{self.get_recommendation_display()} ({self.confidence:.2f})"

    @property
    def blocks_approval(self) -> bool:
        """この評価結果で、成果物の承認を止めるべきか。

        根拠不足のまま承認へ進ませないための判定。PoC 要件の
        「ハルシネーション時の承認前ブロック」に対応する。
        """

        return self.recommendation == Recommendation.ASK_CLARIFICATION or self.has_conflict


class HumanReview(TimeStampedModel):
    """AI 出力に対する人の判断（REQ-AG-010）。

    AI が出した内容と、人が確認・修正した内容を必ず区別して残す。
    """

    class Decision(models.TextChoices):
        PENDING = "pending", "未確認"
        ACCEPTED = "accepted", "採用"
        MODIFIED = "modified", "修正して採用"
        REJECTED = "rejected", "不採用"

    run = models.ForeignKey(
        AgentRun,
        verbose_name="実行",
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    reviewer = models.ForeignKey(
        "accounts.User",
        verbose_name="確認者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_reviews",
    )
    decision = models.CharField(
        "判断",
        max_length=16,
        choices=Decision.choices,
        default=Decision.PENDING,
    )
    comment = models.TextField("コメント", blank=True)
    reviewed_at = models.DateTimeField("確認日時", null=True, blank=True)

    class Meta:
        verbose_name = "人による確認"
        verbose_name_plural = "人による確認"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_decision_display()} by {self.reviewer or '未確認'}"
