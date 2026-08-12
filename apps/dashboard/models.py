"""管制ダッシュボード。ヘルススコア、アラート、介入提案、KPI。

旧実装では `ai_project_control.py` の中で毎回計算していた。予兆検知の
「何営業日前に気づけたか」を後から検証するには、検知した時点のスナップショットを
残す必要があるため、計算結果もテーブルとして保存する。
"""

from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class HealthSnapshot(TimeStampedModel):
    """案件のヘルススコア。日次で 1 レコード。"""

    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="health_snapshots",
    )
    measured_on = models.DateField("計測日")
    schedule_score = models.PositiveSmallIntegerField("進捗スコア", default=0)
    budget_score = models.PositiveSmallIntegerField("予算スコア", default=0)
    quality_score = models.PositiveSmallIntegerField("品質スコア", default=0)
    resource_score = models.PositiveSmallIntegerField("稼働スコア", default=0)
    total_score = models.PositiveSmallIntegerField("総合スコア", default=0)
    breakdown = models.JSONField("算出根拠", default=dict, blank=True)

    class Meta:
        verbose_name = "ヘルススコア"
        verbose_name_plural = "ヘルススコア"
        ordering = ["-measured_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "measured_on"],
                name="uniq_health_snapshot_per_day",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.project.code} {self.measured_on} ({self.total_score})"


class Alert(TimeStampedModel):
    """予兆検知アラート。

    `detected_at` と `acknowledged_at` を分けて持つのは、AI が検知した時点と
    人が気づいた時点の差（＝先行日数）を測るため。
    """

    class Category(models.TextChoices):
        SCHEDULE = "schedule", "進捗"
        QUALITY = "quality", "品質"
        RISK = "risk", "リスク"
        CHANGE = "change", "変更"
        RESOURCE = "resource", "稼働"

    class Severity(models.TextChoices):
        INFO = "info", "情報"
        WARNING = "warning", "注意"
        CRITICAL = "critical", "重大"

    class Status(models.TextChoices):
        OPEN = "open", "未対応"
        ACKNOWLEDGED = "acknowledged", "確認済み"
        RESOLVED = "resolved", "解消"
        DISMISSED = "dismissed", "対象外"

    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="alerts",
    )
    category = models.CharField("分類", max_length=16, choices=Category.choices)
    severity = models.CharField("重要度", max_length=16, choices=Severity.choices, default=Severity.WARNING)
    status = models.CharField("状態", max_length=16, choices=Status.choices, default=Status.OPEN)
    title = models.CharField("件名", max_length=300)
    detail = models.TextField("詳細", blank=True)
    detected_at = models.DateTimeField("検知日時")
    acknowledged_at = models.DateTimeField("確認日時", null=True, blank=True)
    is_pinned = models.BooleanField("ピン留め", default=False)
    evidence = models.JSONField(
        "根拠",
        default=dict,
        blank=True,
        help_text="どのデータから検知したか。監査時に AI の主張と根拠を突き合わせる。",
    )

    class Meta:
        verbose_name = "アラート"
        verbose_name_plural = "アラート"
        ordering = ["-detected_at"]
        indexes = [models.Index(fields=["project", "status", "severity"])]

    def __str__(self) -> str:
        return self.title

    @property
    def lead_time_days(self) -> int | None:
        """検知から人が確認するまでの日数。予兆検知の先行性の実測値。"""

        if self.acknowledged_at is None:
            return None

        return (self.acknowledged_at - self.detected_at).days


class InterventionProposal(TimeStampedModel):
    """AI 介入提案。採否を人が決め、その結果を残す。"""

    class Status(models.TextChoices):
        PROPOSED = "proposed", "提案中"
        ACCEPTED = "accepted", "採用"
        MODIFIED = "modified", "修正して採用"
        REJECTED = "rejected", "不採用"
        DONE = "done", "実施済み"

    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="interventions",
    )
    alert = models.ForeignKey(
        Alert,
        verbose_name="対応アラート",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interventions",
    )
    title = models.CharField("提案", max_length=300)
    rationale = models.TextField("提案理由", blank=True)
    evidence = models.JSONField(
        "根拠",
        default=list,
        blank=True,
        help_text="提案の裏付け。RAG 引用は chunk_key、実データは対象レコード ID で残す。",
    )
    confidence = models.FloatField(
        "信頼度",
        null=True,
        blank=True,
        help_text="0.0-1.0。AI 未使用のルールベース提案では null。",
    )
    recommended_action = models.CharField("推奨アクション", max_length=300, blank=True)
    expected_effect = models.TextField("期待効果", blank=True)
    status = models.CharField("状態", max_length=16, choices=Status.choices, default=Status.PROPOSED)
    decided_by = models.ForeignKey(
        "accounts.User",
        verbose_name="判断者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="intervention_decisions",
    )
    decided_at = models.DateTimeField("判断日時", null=True, blank=True)
    decision_reason = models.TextField("判断理由", blank=True)
    modified_action = models.TextField(
        "修正後のアクション",
        blank=True,
        help_text="「修正して採用」の場合に、人が書き換えた内容。AI 出力と区別して残す。",
    )
    agent_run = models.ForeignKey(
        "agents.AgentRun",
        verbose_name="生成元の実行",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interventions",
    )

    class Meta:
        verbose_name = "AI介入提案"
        verbose_name_plural = "AI介入提案"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class KpiMeasurement(TimeStampedModel):
    """PoC の効果測定値。

    「レポート作業時間 50% 以上削減」「赤字率 20% 未満」「事実誤認 0 件」を
    同じ形で測れるよう、基準値・実績値・目標を 1 テーブルにまとめる。
    """

    class Kind(models.TextChoices):
        REPORT_HOURS = "report_hours", "レポート作業時間"
        CORRECTION_RATE = "correction_rate", "赤字率（修正率）"
        FACT_ERROR_COUNT = "fact_error_count", "事実誤認件数"
        DETECTION_LEAD_DAYS = "detection_lead_days", "予兆検知の先行日数"

    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="kpi_measurements",
    )
    kind = models.CharField("指標", max_length=32, choices=Kind.choices)
    measured_on = models.DateField("計測日")
    baseline_value = models.DecimalField("基準値", max_digits=12, decimal_places=3, null=True, blank=True)
    actual_value = models.DecimalField("実績値", max_digits=12, decimal_places=3)
    target_value = models.DecimalField("目標値", max_digits=12, decimal_places=3, null=True, blank=True)
    unit = models.CharField("単位", max_length=32, blank=True)
    note = models.TextField("備考", blank=True)

    class Meta:
        verbose_name = "KPI実績"
        verbose_name_plural = "KPI実績"
        ordering = ["-measured_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "kind", "measured_on"],
                name="uniq_kpi_per_day",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.measured_on}"

    @property
    def improvement_rate(self) -> float | None:
        """基準値からの改善率。基準値がなければ算出しない。"""

        if self.baseline_value in (None, 0):
            return None

        return float((self.baseline_value - self.actual_value) / self.baseline_value)
