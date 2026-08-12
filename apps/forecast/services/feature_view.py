"""LDF-04: 機能の状態・着地詳細。

「いま誰が何をしているか」と「いつ何日遅れる／前倒しになるか」を、
原文確認を含めて 1 画面で答えるための組み立て。

事実（Signal）・推定（予測）・判断（人の確認）を混ぜない。表示側で色を変えるのではなく、
ここで区分を持たせる。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.contrib.contenttypes.models import ContentType

from apps.forecast.models.signals import Signal
from apps.forecast.models.snapshots import ForecastSnapshot, Horizon
from apps.forecast.services.engine import TaskForecast, compute_project_forecast
from apps.graph.models.graph import WorkLink
from apps.graph.ontology import LinkState, RelationType
from apps.graph.services.queries import build_impact
from apps.projects.models import Defect, WbsTask

#: 根拠タイムラインに出す件数。全文を並べる画面にしない。
EVIDENCE_LIMIT = 20


@dataclass(frozen=True)
class EvidenceItem:
    """根拠タイムラインの 1 行。原文へ戻れることを必須にする。"""

    signal: Signal
    link_state: str
    #: 候補を確定・否定するための関連 ID（AH-07）。
    link_id: object = None

    @property
    def is_confirmed(self) -> bool:
        return self.link_state == LinkState.CONFIRMED

    @property
    def tone(self) -> str:
        return "b" if self.is_confirmed else "a"

    @property
    def state_label(self) -> str:
        return "確認済み" if self.is_confirmed else "未確認の候補"


@dataclass(frozen=True)
class FeatureDetail:
    """機能詳細画面が必要とするものすべて。"""

    feature: object
    horizons: tuple = ()
    tasks: tuple[TaskForecast, ...] = ()
    defects: tuple[Defect, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    impact_nodes: tuple = ()
    unconfirmed_links: int = 0

    @property
    def has_forecast(self) -> bool:
        return bool(self.horizons)

    @property
    def blocked_tasks(self) -> tuple[TaskForecast, ...]:
        return tuple(item for item in self.tasks if item.task.status == WbsTask.Status.BLOCKED)

    @property
    def undeterminable_tasks(self) -> tuple[TaskForecast, ...]:
        return tuple(item for item in self.tasks if item.is_undeterminable)

    @property
    def next_action(self) -> str:
        """次に何をすべきか。推測せず、不足している入力をそのまま返す。"""

        if self.undeterminable_tasks:
            first = self.undeterminable_tasks[0]
            labels = "・".join(first.missing_inputs) or "入力不足"
            return f"「{first.task.name}」の {labels} を解消してください。"
        if self.blocked_tasks:
            task = self.blocked_tasks[0].task
            holder = task.ball_holder or task.owner or "担当未設定"
            return f"「{task.name}」のブロック解消。次に動くのは {holder} です。"
        if self.unconfirmed_links:
            return f"未確認の関連が {self.unconfirmed_links} 件あります。確定または否定してください。"
        return "未確認事項はありません。"


def build_feature_detail(feature, as_of: date) -> FeatureDetail:
    """機能 1 件分の現在地・根拠・着地を組み立てる。"""

    computed = compute_project_forecast(feature.project, as_of)
    task_ids = _linked_task_ids(feature)
    tasks = tuple(
        computed.tasks[task_id] for task_id in task_ids if task_id in computed.tasks
    )

    milestone_ids = _milestone_ids_for(task_ids)
    horizons = tuple(
        target
        for target in computed.targets
        if getattr(target.target, "pk", None) in milestone_ids
    )

    impact = build_impact(feature)
    evidence = _evidence_for(feature)
    # 影響の候補と、根拠タイムライン上の未確認候補の両方を数える。
    # 片方だけ数えると「未確認なし」と表示されるのに候補が画面に残る。
    unconfirmed = sum(1 for edge in impact.edges if edge.is_candidate) + sum(
        1 for item in evidence if not item.is_confirmed
    )

    return FeatureDetail(
        feature=feature,
        horizons=_ordered_horizons(horizons),
        tasks=tasks,
        defects=_defects_for(feature),
        evidence=evidence,
        impact_nodes=impact.nodes,
        unconfirmed_links=unconfirmed,
    )


def latest_snapshots_for(target) -> dict:
    """対象の最新スナップショット。前回との差の表示に使う。"""

    return {
        horizon: ForecastSnapshot.objects.latest_for(target, horizon)
        for horizon in Horizon.values
    }


def _ordered_horizons(horizons) -> tuple:
    order = {Horizon.TWO_DAYS: 0, Horizon.ONE_WEEK: 1, Horizon.MILESTONE: 2}
    return tuple(sorted(horizons, key=lambda item: order.get(item.horizon, 9)))


def _linked_task_ids(feature) -> tuple:
    """機能に紐づく WBS。確定リンクだけを使う（候補で工程を語らない）。"""

    task_type = ContentType.objects.get_for_model(WbsTask)
    links = WorkLink.objects.confirmed().filter(
        project=feature.project,
        relation_type=RelationType.IMPLEMENTS,
        from_content_type=task_type,
        to_object_id=feature.pk,
    )
    return tuple(link.from_object_id for link in links)


def _milestone_ids_for(task_ids) -> set:
    from apps.graph.models.schedule import MilestoneTaskLink

    return set(
        MilestoneTaskLink.objects.filter(task_id__in=task_ids, is_required=True).values_list(
            "milestone_id", flat=True
        )
    )


def _defects_for(feature) -> tuple:
    """この機能に影響する未解決の不具合。候補も含めるが、状態は画面で区別する。"""

    defect_type = ContentType.objects.get_for_model(Defect)
    links = WorkLink.objects.filter(
        project=feature.project,
        relation_type__in=(RelationType.IMPACTS, RelationType.BLOCKS),
        from_content_type=defect_type,
        to_object_id=feature.pk,
    ).exclude(state__in=(LinkState.REJECTED, LinkState.OBSOLETE))

    defect_ids = [link.from_object_id for link in links]
    return tuple(
        Defect.objects.filter(pk__in=defect_ids).exclude(status=Defect.Status.CLOSED)
    )


def _evidence_for(feature) -> tuple[EvidenceItem, ...]:
    """根拠タイムライン。確定と候補を並べたうえで、状態を必ず添える。"""

    signal_type = ContentType.objects.get_for_model(Signal)
    links = WorkLink.objects.filter(
        project=feature.project,
        relation_type__in=(RelationType.DISCUSSED_IN, RelationType.EVIDENCED_BY),
        to_content_type=signal_type,
        from_object_id=feature.pk,
    ).exclude(state__in=(LinkState.REJECTED, LinkState.OBSOLETE))

    states = {link.to_object_id: (link.state, link.pk) for link in links}
    signals = Signal.objects.filter(pk__in=states).order_by("-occurred_at")[:EVIDENCE_LIMIT]
    return tuple(
        EvidenceItem(
            signal=signal,
            link_state=states[signal.pk][0],
            link_id=states[signal.pk][1],
        )
        for signal in signals
    )
