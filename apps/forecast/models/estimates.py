"""LDF-03 の入力: 確認済みの解消見込み。

`docs/改善に.md`:「解消見込みは、責任者が確認した日時を最優先する。未確認の会話・
AI 候補からは日時を作らない。」

そのため `confirmed_by` を必須にする。確認者のいない見込みはこのテーブルに存在できず、
予測エンジンは「未確認」を欠損として扱える。
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models

from apps.projects.models import ProjectScopedModel


class ResolutionEstimateQuerySet(models.QuerySet):
    def for_targets(self, targets) -> dict:
        """対象ごとの最新の見込みをまとめて引く。対象ごとに引くと N+1 になる。"""

        by_type: dict[int, list] = {}
        for target in targets:
            content_type = ContentType.objects.get_for_model(target)
            by_type.setdefault(content_type.pk, []).append(target.pk)

        query = models.Q()
        for content_type_id, object_ids in by_type.items():
            query |= models.Q(
                target_content_type_id=content_type_id, target_object_id__in=object_ids
            )
        if not by_type:
            return {}

        latest: dict = {}
        for estimate in self.filter(query).order_by("confirmed_at"):
            latest[(estimate.target_content_type_id, estimate.target_object_id)] = estimate
        return latest


class ResolutionEstimate(ProjectScopedModel):
    """不具合の修正・再試験、タスクの完了について、責任者が確認した見込み日。"""

    class Kind(models.TextChoices):
        FIX = "fix", "修正完了"
        RETEST = "retest", "再試験完了"
        TASK_FINISH = "task_finish", "タスク完了"

    target_content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="resolution_estimates"
    )
    target_object_id = models.UUIDField()
    target = GenericForeignKey("target_content_type", "target_object_id")

    kind = models.CharField("種別", max_length=16, choices=Kind.choices)
    expected_date = models.DateField("見込み日")
    confirmed_by = models.ForeignKey(
        "accounts.User",
        verbose_name="確認者",
        on_delete=models.PROTECT,
        related_name="resolution_estimates",
        help_text="未確認の見込みは登録できない。予測はここにある日付だけを使う。",
    )
    confirmed_at = models.DateTimeField("確認日時", db_index=True)
    note = models.CharField("補足", max_length=200, blank=True)

    objects = ResolutionEstimateQuerySet.as_manager()

    class Meta:
        verbose_name = "解消見込み"
        verbose_name_plural = "解消見込み"
        ordering = ["-confirmed_at"]
        indexes = [
            models.Index(fields=["target_content_type", "target_object_id", "-confirmed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.expected_date}"

    def save(self, *args, **kwargs):
        # project は clean() が対象から決めるため、必須検証の対象から外す。
        self.full_clean(exclude=["project"])
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        target_project = getattr(self.target, "project", None)
        if target_project is None:
            raise ValidationError("案件に属さない対象へは見込みを登録できません。")
        if self.project_id and self.project_id != target_project.pk:
            raise ValidationError("見込みの案件が対象の案件と一致しません。")
        if not self.project_id:
            self.project = target_project
