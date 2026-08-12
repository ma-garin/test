"""GE-05: グラフ品質の指標と、データ整備キュー。

予測が弱い理由を「AI の精度」の話にしない。どの関係が欠けているから算定できないのかを
数え、担当・期限をつけて直せる作業として出す。

`docs/改善に.md`「グラフ品質の指標と停止条件」の 5 指標をそのまま実装する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.graph.models.graph import Component, Feature, WorkLink
from apps.graph.models.schedule import MilestoneTaskLink, TaskDependency
from apps.graph.ontology import LinkState, RelationType
from apps.projects.models import Defect, Milestone, Severity, WbsTask

#: 重要エッジが「古い」と見なされるまでの日数。
EDGE_FRESHNESS_DAYS = 30

#: 指標が悪化したと判定する閾値。下回ると予測を低確信度または算定不能にする。
DEPENDENCY_COVERAGE_THRESHOLD = 0.6
IMPACT_COVERAGE_THRESHOLD = 0.6


@dataclass(frozen=True)
class Metric:
    """1 つの品質指標。分子・分母を持ち、割合だけを独り歩きさせない。"""

    key: str
    label: str
    numerator: int
    denominator: int
    threshold: float | None = None
    #: 悪化したときに何が起きるか。数値だけを見せない。
    consequence: str = ""

    @property
    def ratio(self) -> float | None:
        return round(self.numerator / self.denominator, 2) if self.denominator else None

    @property
    def is_measurable(self) -> bool:
        return self.denominator > 0

    @property
    def has_threshold(self) -> bool:
        """基準を持つ指標か。持たない指標を「基準内」と表示しない。"""

        return self.threshold is not None

    @property
    def is_degraded(self) -> bool:
        if self.ratio is None or self.threshold is None:
            return False
        return self.ratio < self.threshold

    @property
    def verdict(self) -> str:
        """画面に出す判定。基準の無い指標は参考値として区別する。"""

        if not self.is_measurable:
            return "対象なし"
        if not self.has_threshold:
            return "参考値"
        return "基準未満" if self.is_degraded else "基準内"

    @property
    def display(self) -> str:
        if not self.is_measurable:
            return "対象なし"
        return f"{self.numerator}/{self.denominator}（{int(self.ratio * 100)}%）"


@dataclass(frozen=True)
class RepairItem:
    """データ整備キューの 1 件。何を直せばよいかを名指しする。"""

    kind: str
    label: str
    target: str
    reason: str
    url_name: str = ""
    target_id: object = None


@dataclass(frozen=True)
class GraphQualityReport:
    """案件 1 件分のグラフ品質。"""

    project: object
    metrics: tuple[Metric, ...] = ()
    repairs: tuple[RepairItem, ...] = ()
    cycles: tuple[tuple[str, ...], ...] = ()

    @property
    def degraded(self) -> tuple[Metric, ...]:
        return tuple(metric for metric in self.metrics if metric.is_degraded)

    @property
    def blocks_forecast(self) -> bool:
        """予測を止めるべき状態か。循環依存があれば必ず止める。"""

        return bool(self.cycles) or bool(self.degraded)

    def metric(self, key: str) -> Metric | None:
        for metric in self.metrics:
            if metric.key == key:
                return metric
        return None


def build_quality_report(project) -> GraphQualityReport:
    """グラフの品質を測り、整備すべき対象を返す。"""

    metrics = (
        _dependency_coverage(project),
        _impact_coverage(project),
        _edge_freshness(project),
        _isolated_nodes(project),
    )
    cycles = detect_cycles(project)
    repairs = _build_repairs(project, cycles)

    return GraphQualityReport(
        project=project, metrics=metrics, repairs=repairs, cycles=cycles
    )


def detect_cycles(project) -> tuple[tuple[str, ...], ...]:
    """`depends_on` グラフの閉路。あれば予測を止め、経路を出す。"""

    edges = list(
        TaskDependency.objects.filter(project=project).values_list(
            "predecessor__wbs_code", "successor__wbs_code"
        )
    )
    successors: dict[str, list[str]] = {}
    for predecessor, successor in edges:
        successors.setdefault(predecessor, []).append(successor)

    found: list[tuple[str, ...]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(node: str, path: tuple[str, ...]) -> None:
        if node in visiting:
            start = path.index(node)
            found.append((*path[start:], node))
            return
        if node in visited:
            return
        visiting.add(node)
        for nxt in successors.get(node, ()):
            walk(nxt, (*path, node))
        visiting.discard(node)
        visited.add(node)

    for node in list(successors):
        walk(node, ())
    return tuple(found)


# ── 指標 ───────────────────────────────────────────────────


def _dependency_coverage(project) -> Metric:
    """着地予測の対象 WBS のうち、先行／後続とマイルストーンが確認済みの割合。"""

    linked_task_ids = set(
        MilestoneTaskLink.objects.filter(project=project, is_required=True).values_list(
            "task_id", flat=True
        )
    )
    total = WbsTask.objects.filter(project=project).exclude(
        status__in=(WbsTask.Status.DONE, WbsTask.Status.ARCHIVED)
    )
    denominator = total.count()
    numerator = total.filter(pk__in=linked_task_ids).count()

    return Metric(
        key="dependency_coverage",
        label="依存充足率",
        numerator=numerator,
        denominator=denominator,
        threshold=DEPENDENCY_COVERAGE_THRESHOLD,
        consequence="閾値未満のとき、日数予測を低確信度または算定不能にします。",
    )


def _impact_coverage(project) -> Metric:
    """重大不具合のうち、機能・WBS・テストのリンクが確認済みまたは否定済みの割合。"""

    critical = Defect.objects.filter(
        project=project, severity__in=(Severity.CRITICAL, Severity.HIGH)
    ).exclude(status=Defect.Status.CLOSED)
    denominator = critical.count()
    if denominator == 0:
        return Metric(
            key="impact_coverage",
            label="影響確認率",
            numerator=0,
            denominator=0,
            consequence="AI候補だけで影響グラフを確定表示しません。",
        )

    defect_type = ContentType.objects.get_for_model(Defect)
    reviewed_ids = set(
        WorkLink.objects.filter(
            project=project,
            from_content_type=defect_type,
            from_object_id__in=[d.pk for d in critical],
            state__in=(LinkState.CONFIRMED, LinkState.REJECTED),
        ).values_list("from_object_id", flat=True)
    )

    return Metric(
        key="impact_coverage",
        label="影響確認率",
        numerator=len(reviewed_ids),
        denominator=denominator,
        threshold=IMPACT_COVERAGE_THRESHOLD,
        consequence="AI候補だけで影響グラフを確定表示しません。",
    )


def _edge_freshness(project) -> Metric:
    """重要エッジのうち、最終確認が新しいものの割合。"""

    boundary = timezone.now() - timedelta(days=EDGE_FRESHNESS_DAYS)
    confirmed = WorkLink.objects.filter(project=project, state=LinkState.CONFIRMED)
    denominator = confirmed.count()
    numerator = confirmed.filter(confirmed_at__gte=boundary).count()

    return Metric(
        key="edge_freshness",
        label="エッジ鮮度",
        numerator=numerator,
        denominator=denominator,
        consequence=f"{EDGE_FRESHNESS_DAYS}日を超えた関係は「要再確認」として扱えます。",
    )


def _isolated_nodes(project) -> Metric:
    """機能・技術要素のうち、必要な関係を持つものの割合。"""

    features = Feature.objects.filter(project=project)
    components = Component.objects.filter(project=project)
    denominator = features.count() + components.count()
    if denominator == 0:
        return Metric(
            key="connected_nodes",
            label="関係を持つノード率",
            numerator=0,
            denominator=0,
            consequence="機能台帳が空のため、機能単位の着地を出せません。",
        )

    linked_ids = set(
        WorkLink.objects.filter(project=project).values_list("to_object_id", flat=True)
    ) | set(WorkLink.objects.filter(project=project).values_list("from_object_id", flat=True))

    numerator = sum(
        1 for node in (*features, *components) if node.pk in linked_ids
    )
    return Metric(
        key="connected_nodes",
        label="関係を持つノード率",
        numerator=numerator,
        denominator=denominator,
        consequence="孤立したノードは根拠のない推定を招くため、整備キューへ出します。",
    )


# ── 整備キュー ─────────────────────────────────────────────


def _build_repairs(project, cycles) -> tuple[RepairItem, ...]:
    repairs: list[RepairItem] = []

    for path in cycles:
        repairs.append(
            RepairItem(
                kind="cycle",
                label="循環依存の解消",
                target=" → ".join(path),
                reason="閉路があると前方向の計算ができず、予測を停止します。",
            )
        )

    milestones = list(Milestone.objects.filter(project=project))
    has_open_tasks = (
        WbsTask.objects.filter(project=project)
        .exclude(status__in=(WbsTask.Status.DONE, WbsTask.Status.ARCHIVED))
        .exists()
    )
    if not milestones and has_open_tasks:
        # 指標が 0% なのに整備キューが空、という食い違いを作らない。
        repairs.append(
            RepairItem(
                kind="milestone",
                label="マイルストーンの登録",
                target=project.name,
                reason="マイルストーンが 1 件も無いため、案件の着地日を算定できません。",
            )
        )

    for milestone in milestones:
        if not MilestoneTaskLink.objects.filter(milestone=milestone, is_required=True).exists():
            repairs.append(
                RepairItem(
                    kind="milestone",
                    label="必須WBSの紐付け",
                    target=milestone.name,
                    reason="必須WBSが無いため、このマイルストーンの着地日を算定できません。",
                    target_id=milestone.pk,
                )
            )

    for task in WbsTask.objects.filter(project=project, planned_end__isnull=True).exclude(
        status__in=(WbsTask.Status.DONE, WbsTask.Status.ARCHIVED)
    )[:20]:
        repairs.append(
            RepairItem(
                kind="task",
                label="計画終了日の登録",
                target=f"{task.wbs_code} {task.name}",
                reason="計画終了日が無いため、終了見込みを算定できません。",
                url_name="projects:task_edit",
                target_id=task.pk,
            )
        )

    for feature in Feature.objects.filter(project=project):
        has_task = WorkLink.objects.filter(
            project=project, relation_type=RelationType.IMPLEMENTS, to_object_id=feature.pk
        ).exists()
        if not has_task:
            repairs.append(
                RepairItem(
                    kind="feature",
                    label="機能とWBSの紐付け",
                    target=feature.name,
                    reason="実装するWBSが未確認のため、機能単位の着地を出せません。",
                    target_id=feature.pk,
                )
            )

    return tuple(repairs)
