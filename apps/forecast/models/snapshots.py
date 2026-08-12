"""LDF-02: 予測スナップショットと、人のレビュー。

予測を上書きすると「なぜ悪化したか」を説明できない。時点ごとの記録として残し、
前回との差と、使った根拠・不足入力をたどれるようにする。

不変条件:
- 確信度が `算定不能` のとき、予測日と差分営業日は持たない（もっともらしい数字を出さない）。
- 予測日を出すなら、根拠（Signal・依存・確認済み見込み）を 1 件以上持つか、
  決定論の計算方法を明示する。
- 不足入力は必ず記録する。空欄と「不足なし」を同じ表示にしない。
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models

from apps.forecast.models.signals import Signal
from apps.projects.models import ProjectScopedModel


class Horizon(models.TextChoices):
    """予測の時点。単に今日から日付を足した表示にはしない。"""

    TWO_DAYS = "2d", "2日後"
    ONE_WEEK = "1w", "1週間後"
    MILESTONE = "milestone", "最終期日"


class Confidence(models.TextChoices):
    """AI の自己評価ではなく、入力品質の指標として計算する。"""

    HIGH = "high", "高"
    MEDIUM = "medium", "中"
    LOW = "low", "低"
    UNKNOWN = "unknown", "算定不能"


class MissingInput(models.TextChoices):
    """算定不能の理由。次に埋めるべき入力を利用者へ返すために型で持つ。"""

    NO_MILESTONE = "no_milestone", "マイルストーン未登録"
    NO_MILESTONE_TASKS = "no_milestone_tasks", "マイルストーンに必須WBSが未紐付け"
    NO_PLANNED_END = "no_planned_end", "WBSの計画終了日が未設定"
    NO_CALENDAR = "no_calendar", "勤務カレンダー未設定"
    NO_DEPENDENCY = "no_dependency", "先行タスクが未登録"
    UNRESOLVED_BLOCKER = "unresolved_blocker", "ブロッカーの解消見込みが未確認"
    CYCLIC_DEPENDENCY = "cyclic_dependency", "循環依存"
    STALE_SIGNAL = "stale_signal", "情報が鮮度切れ"
    NO_FEATURE_LINK = "no_feature_link", "機能とWBSの紐付けが未確認"


class ForecastSnapshotQuerySet(models.QuerySet):
    def latest_for(self, target, horizon: str):
        return (
            self.filter(
                target_content_type=ContentType.objects.get_for_model(target),
                target_object_id=target.pk,
                horizon=horizon,
            )
            .order_by("-as_of")
            .first()
        )

    def undeterminable(self) -> ForecastSnapshotQuerySet:
        return self.filter(confidence=Confidence.UNKNOWN)


class ForecastSnapshot(ProjectScopedModel):
    """ある時点・ある対象・ある地平の予測 1 件。"""

    #: 決定論の計算方法の版。統計や AI を足したときに、混ぜて比較しないため。
    METHOD_DETERMINISTIC_V1 = "deterministic_v1"

    target_content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="forecast_snapshots"
    )
    target_object_id = models.UUIDField()
    target = GenericForeignKey("target_content_type", "target_object_id")

    as_of = models.DateTimeField("予測時点", db_index=True)
    horizon = models.CharField("地平", max_length=16, choices=Horizon.choices)
    baseline_date = models.DateField("予定日", null=True, blank=True)
    forecast_date = models.DateField("予測日", null=True, blank=True)
    variance_business_days = models.IntegerField(
        "差分営業日",
        null=True,
        blank=True,
        help_text="正が遅延、負が前倒し。算定不能なら None。",
    )
    confidence = models.CharField("確信度", max_length=16, choices=Confidence.choices)
    method = models.CharField("計算方法", max_length=32, default=METHOD_DETERMINISTIC_V1)
    missing_inputs = models.JSONField("不足入力", default=list, blank=True)
    summary = models.CharField("要約", max_length=300, blank=True)
    previous = models.ForeignKey(
        "self",
        verbose_name="前回の予測",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="following",
    )
    evidence = models.ManyToManyField(
        Signal, verbose_name="根拠", through="forecast.ForecastEvidence", related_name="forecasts"
    )

    objects = ForecastSnapshotQuerySet.as_manager()

    class Meta:
        verbose_name = "着地予測"
        verbose_name_plural = "着地予測"
        ordering = ["-as_of"]
        indexes = [
            models.Index(fields=["project", "horizon", "-as_of"]),
            models.Index(fields=["target_content_type", "target_object_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["target_content_type", "target_object_id", "horizon", "as_of"],
                name="forecast_snapshot_unique_point",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(confidence="unknown", forecast_date__isnull=True)
                    | ~models.Q(confidence="unknown")
                ),
                name="forecast_snapshot_unknown_has_no_date",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.target} {self.get_horizon_display()} {self.display_date}"

    @property
    def display_date(self) -> str:
        return self.forecast_date.isoformat() if self.forecast_date else "算定不能"

    @property
    def is_undeterminable(self) -> bool:
        return self.confidence == Confidence.UNKNOWN

    @property
    def is_delayed(self) -> bool:
        return bool(self.variance_business_days and self.variance_business_days > 0)

    @property
    def is_ahead(self) -> bool:
        return bool(self.variance_business_days and self.variance_business_days < 0)

    @property
    def variance_from_previous(self) -> int | None:
        """前回予測からの悪化（正）／改善（負）。前回が無ければ None。"""

        if self.previous is None or self.previous.variance_business_days is None:
            return None
        if self.variance_business_days is None:
            return None
        return self.variance_business_days - self.previous.variance_business_days

    def missing_input_labels(self) -> tuple[str, ...]:
        labels = dict(MissingInput.choices)
        return tuple(labels.get(key, key) for key in self.missing_inputs)

    def clean(self) -> None:
        super().clean()
        if self.confidence == Confidence.UNKNOWN:
            if self.forecast_date is not None or self.variance_business_days is not None:
                raise ValidationError(
                    "算定不能の予測に日付・日数を持たせることはできません。"
                    "不足入力を missing_inputs に記録してください。"
                )
            if not self.missing_inputs:
                raise ValidationError("算定不能には、不足している入力の記録が必要です。")
        elif self.forecast_date is None:
            raise ValidationError("算定できたのに予測日がありません。確信度を見直してください。")

    def save(self, *args, **kwargs):
        self.full_clean(exclude=["project"])
        return super().save(*args, **kwargs)


class ForecastEvidence(models.Model):
    """予測とその根拠の対応。使わなかった候補も残す。

    「なぜこの予測なのか」に答えるには、使った根拠だけでなく
    「あったが使わなかった候補」も見せる必要がある。
    """

    class Role(models.TextChoices):
        USED = "used", "予測に使用"
        UNUSED_CANDIDATE = "unused_candidate", "未確認のため不使用"
        CONTRADICTS = "contradicts", "矛盾する情報"

    snapshot = models.ForeignKey(
        ForecastSnapshot, on_delete=models.CASCADE, related_name="evidence_links"
    )
    signal = models.ForeignKey(Signal, on_delete=models.CASCADE, related_name="evidence_links")
    role = models.CharField("役割", max_length=24, choices=Role.choices, default=Role.USED)
    note = models.CharField("補足", max_length=200, blank=True)

    class Meta:
        verbose_name = "予測の根拠"
        verbose_name_plural = "予測の根拠"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "signal"], name="forecast_evidence_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_role_display()}: {self.signal.summary}"


class ForecastReview(models.Model):
    """PMO の確認結果。予測の改善と、実績との比較に使う。"""

    class Decision(models.TextChoices):
        ADOPT = "adopt", "採用"
        CORRECT = "correct", "修正して採用"
        REJECT = "reject", "却下"

    snapshot = models.ForeignKey(
        ForecastSnapshot, on_delete=models.CASCADE, related_name="reviews"
    )
    reviewer = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, related_name="forecast_reviews"
    )
    decision = models.CharField("判断", max_length=16, choices=Decision.choices)
    reason = models.CharField("理由", max_length=300, blank=True)
    corrected_date = models.DateField("修正後の予測日", null=True, blank=True)
    created_at = models.DateTimeField("判断日時", auto_now_add=True)

    class Meta:
        verbose_name = "予測レビュー"
        verbose_name_plural = "予測レビュー"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_decision_display()} by {self.reviewer}"

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.decision == self.Decision.CORRECT and self.corrected_date is None:
            raise ValidationError("修正して採用するには、修正後の予測日が必要です。")
        if self.decision == self.Decision.REJECT and not self.reason:
            raise ValidationError("却下には理由が必要です。")
