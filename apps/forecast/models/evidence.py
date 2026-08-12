"""LDF-05: テスト証跡。

QA の実施結果が無いと、「QA工程がどこまで進んだか」を推定するしかなくなる。
推定しないために、テスト種別・対象・結果・実施時刻・再試験予定を明示的に持つ。

外部のテスト管理ツールが無い顧客でも運用できるよう、CSV と手動登録を一級の入口にする
（`docs/改善に.md`:「手入力・CSVを軽視しない。予測の土台である」）。
"""

from __future__ import annotations

from django.db import models

from apps.projects.models import ProjectScopedModel


class TestEvidence(ProjectScopedModel):
    """テスト実行 1 件の記録。結果と再試験予定を分けて持つ。"""

    class Kind(models.TextChoices):
        UNIT = "unit", "単体"
        INTEGRATION = "integration", "結合"
        SYSTEM = "system", "システム"
        UAT = "uat", "受入"
        REGRESSION = "regression", "回帰"

    class Result(models.TextChoices):
        PASSED = "passed", "成功"
        FAILED = "failed", "失敗"
        BLOCKED = "blocked", "実施不可"
        SKIPPED = "skipped", "未実施"

    class Origin(models.TextChoices):
        CSV = "csv", "CSV取込"
        MANUAL = "manual", "手動登録"
        CONNECTOR = "connector", "テスト管理ツール"

    external_id = models.CharField("外部ID", max_length=200)
    name = models.CharField("テスト名", max_length=300)
    kind = models.CharField("種別", max_length=16, choices=Kind.choices)
    result = models.CharField("結果", max_length=16, choices=Result.choices)
    executed_at = models.DateTimeField("実施時刻", db_index=True)
    environment = models.CharField("環境", max_length=120, blank=True)
    failure_reason = models.CharField("失敗理由", max_length=300, blank=True)
    external_url = models.URLField("外部URL", max_length=500, blank=True)
    retest_planned_on = models.DateField(
        "再試験予定日",
        null=True,
        blank=True,
        help_text="未登録なら、QA工程の完了時期を推定しない（算定不能の理由になる）。",
    )
    defect_reference = models.CharField("関連不具合の外部キー", max_length=120, blank=True)
    feature = models.ForeignKey(
        "graph.Feature",
        verbose_name="対象機能",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="test_evidence",
    )
    origin = models.CharField("入力元", max_length=16, choices=Origin.choices, default=Origin.CSV)

    class Meta:
        verbose_name = "テスト証跡"
        verbose_name_plural = "テスト証跡"
        ordering = ["-executed_at"]
        indexes = [models.Index(fields=["project", "result", "-executed_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "external_id"], name="forecast_test_evidence_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name}（{self.get_result_display()}）"

    @property
    def is_failure(self) -> bool:
        return self.result in (self.Result.FAILED, self.Result.BLOCKED)

    @property
    def blocks_completion(self) -> bool:
        """QA 工程の完了を妨げるか。失敗していて再試験予定も無い状態を指す。"""

        return self.is_failure and self.retest_planned_on is None
