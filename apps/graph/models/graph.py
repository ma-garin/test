"""GE-01: 機能台帳・技術要素と、型付きの関連（`WorkLink`）。

既存の `WbsTask.related_tasks` は対称の多対多で、方向・関係の意味・出所・確認状態を
持てない。PMO が「この不具合はどこへ影響するか」「なぜこの予測なのか」を機械的に
たどるには、関係そのものを 1 レコードとして持つ必要がある。

保存時の不変条件:
- 関係型はオントロジーで許可されたものだけ。
- 両端のノード種別の組み合わせも許可されたものだけ。
- 両端は同じ案件に属する。案件をまたぐ場合は明示フラグが要り、テナントは必ず一致する。
- 出所（provenance）は必須。AI 候補・規則由来は、人が確認するまで `confirmed` にできない。
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.graph.ontology import (
    AUTO_CONFIRMABLE,
    LinkState,
    Provenance,
    RelationType,
    endpoint_label,
    is_allowed,
)
from apps.projects.models import Project, ProjectScopedModel


class Feature(ProjectScopedModel):
    """PMO が把握する業務機能。画面・API・バッチ・テスト・WBS を束ねる単位。

    WBS や文書名を流用しない。WBS は「作業」、機能は「顧客に渡すもの」であり、
    1 機能が複数の WBS にまたがるため、同じ台帳にすると着地予測の対象が定まらない。
    """

    class Status(models.TextChoices):
        PLANNED = "planned", "計画中"
        IN_DEVELOPMENT = "in_development", "開発中"
        IN_TEST = "in_test", "試験中"
        READY = "ready", "リリース準備完了"
        RELEASED = "released", "リリース済み"
        DROPPED = "dropped", "対象外"

    name = models.CharField("機能名", max_length=200)
    description = models.TextField("説明", blank=True)
    owner = models.CharField("責任者", max_length=120, blank=True)
    release_target = models.CharField(
        "リリース対象",
        max_length=120,
        blank=True,
        help_text="リリース回・バージョン。マイルストーンとの紐付けは MilestoneTaskLink で持つ。",
    )
    status = models.CharField("状態", max_length=32, choices=Status.choices, default=Status.PLANNED)

    class Meta:
        verbose_name = "機能"
        verbose_name_plural = "機能"
        ordering = ["project__code", "name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], name="graph_feature_unique_name"),
        ]

    def __str__(self) -> str:
        return self.name


class Component(ProjectScopedModel):
    """影響範囲を表す技術要素。最初から構成管理DBを作らず、重大機能から手動で増やす。"""

    class Kind(models.TextChoices):
        SCREEN = "screen", "画面"
        FRONTEND = "frontend", "FE"
        BACKEND = "backend", "BE"
        API = "api", "API"
        BATCH = "batch", "バッチ"
        DATABASE = "database", "DB"
        TEST = "test", "テスト"

    name = models.CharField("名称", max_length=200)
    kind = models.CharField("種別", max_length=32, choices=Kind.choices)
    owner = models.CharField("所有チーム", max_length=120, blank=True)

    class Meta:
        verbose_name = "技術要素"
        verbose_name_plural = "技術要素"
        ordering = ["project__code", "kind", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "kind", "name"], name="graph_component_unique_name"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.name}"


class WorkLinkQuerySet(models.QuerySet):
    def confirmed(self) -> WorkLinkQuerySet:
        """予測の確定根拠に使える関連だけ。候補・否定・失効は除く。"""
        return self.filter(state=LinkState.CONFIRMED)

    def candidates(self) -> WorkLinkQuerySet:
        return self.filter(state=LinkState.CANDIDATE)

    def valid_at(self, moment) -> WorkLinkQuerySet:
        """その時点で有効な関連。修正済み不具合の古い影響を現在の影響にしない。"""
        return self.filter(
            models.Q(valid_from__isnull=True) | models.Q(valid_from__lte=moment),
            models.Q(valid_to__isnull=True) | models.Q(valid_to__gte=moment),
        )


class WorkLink(ProjectScopedModel):
    """型・方向・状態・出所・有効期間を持つ関連。

    `from_object` → `to_object` の有向エッジ。相互参照が必要でも、計算上の方向を
    曖昧にしない（曖昧な方向は遅延の伝播先を誤らせる）。
    """

    relation_type = models.CharField("関係型", max_length=32, choices=RelationType.choices)
    state = models.CharField(
        "確認状態", max_length=16, choices=LinkState.choices, default=LinkState.CANDIDATE
    )
    provenance = models.CharField("出所", max_length=32, choices=Provenance.choices)
    source_reference = models.CharField(
        "出所の参照",
        max_length=300,
        blank=True,
        help_text="外部ID・規則名・Signal識別子など。秘密情報や本文は入れない。",
    )

    from_content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="graph_links_from"
    )
    from_object_id = models.UUIDField()
    from_object = GenericForeignKey("from_content_type", "from_object_id")

    to_content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="graph_links_to"
    )
    to_object_id = models.UUIDField()
    to_object = GenericForeignKey("to_content_type", "to_object_id")

    is_cross_project = models.BooleanField(
        "案件横断",
        default=False,
        help_text="明示的に許可された案件間依存だけ True。テナントは必ず一致する。",
    )
    confidence = models.DecimalField(
        "確信度", max_digits=4, decimal_places=3, null=True, blank=True
    )
    valid_from = models.DateTimeField("有効開始", null=True, blank=True)
    valid_to = models.DateTimeField("有効終了", null=True, blank=True)
    confirmed_by = models.ForeignKey(
        "accounts.User",
        verbose_name="確認者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_work_links",
    )
    confirmed_at = models.DateTimeField("確認日時", null=True, blank=True)
    review_reason = models.CharField("確認・否定の理由", max_length=300, blank=True)

    objects = WorkLinkQuerySet.as_manager()

    class Meta:
        verbose_name = "関連"
        verbose_name_plural = "関連"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "relation_type", "state"]),
            models.Index(fields=["from_content_type", "from_object_id"]),
            models.Index(fields=["to_content_type", "to_object_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "relation_type",
                    "from_content_type",
                    "from_object_id",
                    "to_content_type",
                    "to_object_id",
                ],
                name="graph_worklink_unique_edge",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.from_object} -{self.relation_type}-> {self.to_object}"

    # ── 検証 ────────────────────────────────────────────────
    def clean(self) -> None:
        super().clean()
        from_obj, to_obj = self.from_object, self.to_object
        if from_obj is None or to_obj is None:
            raise ValidationError("関連の両端が解決できません。削除済みの対象を指しています。")

        self._validate_relation(from_obj, to_obj)
        self._validate_scope(from_obj, to_obj)
        self._validate_state()

    def _validate_relation(self, from_obj, to_obj) -> None:
        from_label, to_label = endpoint_label(from_obj), endpoint_label(to_obj)
        if not is_allowed(self.relation_type, from_label, to_label):
            raise ValidationError(
                f"{self.relation_type} は {from_label} → {to_label} に使えません。"
                "オントロジーに登録された組み合わせだけを保存できます。"
            )
        if from_label == to_label and self.from_object_id == self.to_object_id:
            raise ValidationError("同じ対象を自分自身へ関連付けることはできません。")

    def _validate_scope(self, from_obj, to_obj) -> None:
        from_project, to_project = _project_of(from_obj), _project_of(to_obj)
        if from_project is None or to_project is None:
            raise ValidationError("案件に属さない対象は関連付けできません。")

        if from_project.tenant_id != to_project.tenant_id:
            raise ValidationError("テナントをまたぐ関連は作れません。")

        if from_project.pk != to_project.pk:
            if not self.is_cross_project:
                raise ValidationError(
                    "案件をまたぐ関連には is_cross_project の明示が必要です。"
                )
        elif self.project_id and self.project_id != from_project.pk:
            raise ValidationError("関連の案件が、両端の案件と一致しません。")

        if not self.project_id:
            self.project = from_project

    def _validate_state(self) -> None:
        if not self.provenance:
            raise ValidationError("出所のない関連は保存できません。")

        needs_human = self.provenance not in AUTO_CONFIRMABLE
        if self.state == LinkState.CONFIRMED and needs_human and self.confirmed_by_id is None:
            raise ValidationError(
                "AI候補・規則・Signal 由来の関連は、人が確認するまで確定にできません。"
            )

    def save(self, *args, **kwargs):
        """検証を通らない関連はそのまま保存できないようにする。

        Django は既定では `save()` で検証しない。ここを緩めると、画面や連携の
        どれか 1 か所の抜けから、型のない関連がグラフへ入り込む。
        """
        self.full_clean(exclude=self._validation_exclusions())
        return super().save(*args, **kwargs)

    @staticmethod
    def _validation_exclusions() -> list[str]:
        # project は clean() が両端から決めるため、必須検証の対象から外す。
        return ["project"]

    # ── レビュー ─────────────────────────────────────────────
    def confirm(self, user, reason: str = "") -> WorkLink:
        """人の確認。AI が確定させたように見せないため、確認者と時刻を必ず残す。"""
        return self._reviewed(LinkState.CONFIRMED, user, reason)

    def reject(self, user, reason: str = "") -> WorkLink:
        return self._reviewed(LinkState.REJECTED, user, reason)

    def _reviewed(self, state: str, user, reason: str) -> WorkLink:
        self.state = state
        self.confirmed_by = user
        self.confirmed_at = timezone.now()
        self.review_reason = reason
        self.save()
        return self


def _project_of(obj) -> Project | None:
    """ノードが属する案件を返す。案件そのものならそれ自身。"""

    if isinstance(obj, Project):
        return obj

    return getattr(obj, "project", None)
