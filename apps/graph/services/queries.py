"""GE-03: 影響・根拠・依存の経路を返す問い合わせサービス。

画面・予測・報告が別々に経路をたどると、同じ不具合に対して違う影響範囲が表示される。
ここを唯一の出所にする。

性能の方針:
- 案件と関係型で絞った 1 回の問い合わせでエッジを読み、Python 側で経路をたどる。
  ノードごとに DB を引くと、深さ 4 の影響探索でそのまま N+1 になる。
- ノードの表示名は、種別ごとにまとめて 1 回ずつ取得する。
- 深さと件数に上限を持つ。上限に当たったことは結果に残し、黙って打ち切らない。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID

from django.contrib.contenttypes.models import ContentType

from apps.graph.models.graph import WorkLink
from apps.graph.models.schedule import MilestoneTaskLink, TaskDependency
from apps.graph.ontology import LinkState, Provenance, RelationType

#: 影響探索でたどる最大の深さ。PMO が読める範囲を超えると、経路は説明に使えない。
MAX_IMPACT_DEPTH = 4

#: 1 回の探索で返すノードの上限。超えた場合は `truncated` を立てる。
MAX_IMPACT_NODES = 300

#: 影響をたどるときに使う関係型。「似ている」関係は含めない。
IMPACT_RELATIONS = (
    RelationType.IMPACTS,
    RelationType.BLOCKS,
    RelationType.IMPLEMENTS,
    RelationType.DEPENDS_ON,
)

#: エッジの向きと影響の向きが逆になる関係型。
#: `Component implements Feature` のとき、機能が壊れれば実装した要素も確認対象になる。
#: ここを分けずに全部を双方向にすると、影響が案件全体へ広がって説明に使えなくなる。
REVERSE_IMPACT_RELATIONS = (RelationType.IMPLEMENTS,)


@dataclass(frozen=True)
class GraphNode:
    """経路上の 1 ノード。表示名まで持たせ、画面側で再取得させない。"""

    label: str
    id: UUID
    title: str

    @property
    def key(self) -> tuple[str, UUID]:
        return (self.label, self.id)


@dataclass(frozen=True)
class GraphEdge:
    """経路上の 1 エッジ。出所と確認状態を必ず持つ。"""

    id: UUID
    relation_type: str
    state: str
    provenance: str
    source_reference: str
    from_node: GraphNode
    to_node: GraphNode
    confirmed_by: str | None
    confirmed_at: object | None

    @property
    def is_confirmed(self) -> bool:
        return self.state == LinkState.CONFIRMED

    @property
    def is_candidate(self) -> bool:
        return self.state == LinkState.CANDIDATE

    @property
    def relation_label(self) -> str:
        """画面に内部キーを出さない。`impacts` ではなく「影響する」と読ませる。"""

        return dict(RelationType.choices).get(self.relation_type, self.relation_type)

    @property
    def provenance_label(self) -> str:
        return dict(Provenance.choices).get(self.provenance, self.provenance)

    @property
    def state_label(self) -> str:
        return dict(LinkState.choices).get(self.state, self.state)


@dataclass(frozen=True)
class ImpactResult:
    """影響探索の結果。確定と候補を混ぜない。"""

    origin: GraphNode
    edges: tuple[GraphEdge, ...] = ()
    nodes: tuple[GraphNode, ...] = ()
    truncated: bool = False
    #: 到達したが、そこから先の関係が 1 本も無いノード（確認漏れの候補）。
    dead_ends: tuple[GraphNode, ...] = ()

    def by_label(self, label: str) -> tuple[GraphNode, ...]:
        return tuple(node for node in self.nodes if node.label == label)

    @property
    def confirmed_edges(self) -> tuple[GraphEdge, ...]:
        return tuple(edge for edge in self.edges if edge.is_confirmed)

    @property
    def candidate_edges(self) -> tuple[GraphEdge, ...]:
        return tuple(edge for edge in self.edges if edge.is_candidate)


@dataclass
class _EdgeIndex:
    """案件 1 件分の隣接表。探索のたびに DB を引かないために作る。"""

    #: 起点キー → [(エッジ, たどる先のキー)]。影響の向きが逆の関係もここで吸収する。
    outgoing: dict[tuple[str, UUID], list[tuple[WorkLink, tuple[str, UUID]]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    labels: dict[int, str] = field(default_factory=dict)


def build_impact(
    origin, *, include_candidates: bool = True, max_depth: int = MAX_IMPACT_DEPTH
) -> ImpactResult:
    """起点から影響方向へたどる。

    `include_candidates=False` にすると、確定リンクだけをたどる。予測の日数計算は
    必ずこちらを使う（候補で納期を動かさないため）。
    """

    project = getattr(origin, "project", None)
    if project is None:
        raise ValueError("案件に属さない対象からは影響をたどれません。")

    index = _load_edges(project, include_candidates=include_candidates)
    origin_node = _node_for(origin)

    visited: dict[tuple[str, UUID], GraphNode] = {origin_node.key: origin_node}
    edges: list[GraphEdge] = []
    frontier = [origin_node.key]
    truncated = False
    reached_targets: set[tuple[str, UUID]] = set()

    for _ in range(max_depth):
        next_frontier: list[tuple[str, UUID]] = []
        for key in frontier:
            for link, target_key in index.outgoing.get(key, ()):
                reached_targets.add(key)
                if len(visited) >= MAX_IMPACT_NODES:
                    truncated = True
                    break
                if target_key not in visited:
                    visited[target_key] = GraphNode(
                        label=target_key[0], id=target_key[1], title=""
                    )
                    next_frontier.append(target_key)
                edges.append(_edge_for(link, index, visited))
            if truncated:
                break
        if truncated or not next_frontier:
            break
        frontier = next_frontier

    titled = _with_titles(visited.values())
    resolved_edges = tuple(_retitle(edge, titled) for edge in edges)
    dead_ends = tuple(
        node for key, node in titled.items() if key not in reached_targets and key != origin_node.key
    )

    return ImpactResult(
        origin=titled[origin_node.key],
        edges=resolved_edges,
        nodes=tuple(titled.values()),
        truncated=truncated,
        dead_ends=dead_ends,
    )


def downstream_tasks(task, *, max_depth: int = 50) -> tuple:
    """WBS の後続を前方向にたどる。クリティカルパスと遅延伝播の入力。

    依存は案件内で閉じているため、案件で絞った 1 回の問い合わせで足りる。
    """

    edges = list(
        TaskDependency.objects.filter(project=task.project).values_list(
            "predecessor_id", "successor_id", "lag_business_days", "dependency_type"
        )
    )
    successors: dict = defaultdict(list)
    for predecessor_id, successor_id, lag, kind in edges:
        successors[predecessor_id].append((successor_id, lag, kind))

    ordered: list[tuple] = []
    seen = {task.pk}
    frontier = [task.pk]
    for _ in range(max_depth):
        nxt = []
        for node in frontier:
            for successor_id, lag, kind in successors.get(node, ()):
                if successor_id in seen:
                    continue
                seen.add(successor_id)
                ordered.append((successor_id, lag, kind))
                nxt.append(successor_id)
        if not nxt:
            break
        frontier = nxt
    return tuple(ordered)


def milestones_for_tasks(task_ids: Iterable[UUID], *, required_only: bool = True) -> dict:
    """WBS からマイルストーンへの紐付けをまとめて引く。

    タスクごとに引くと、後続 30 件の探索で 30 回の問い合わせになる。
    """

    links = MilestoneTaskLink.objects.filter(task_id__in=list(task_ids))
    if required_only:
        links = links.filter(is_required=True)

    mapping: dict = defaultdict(list)
    for task_id, milestone_id in links.values_list("task_id", "milestone_id"):
        mapping[task_id].append(milestone_id)
    return dict(mapping)


# ── 内部 ───────────────────────────────────────────────────


def _load_edges(project, *, include_candidates: bool) -> _EdgeIndex:
    states = (
        (LinkState.CONFIRMED, LinkState.CANDIDATE)
        if include_candidates
        else (LinkState.CONFIRMED,)
    )
    links = list(
        WorkLink.objects.filter(
            project=project, relation_type__in=IMPACT_RELATIONS, state__in=states
        ).select_related("confirmed_by")
    )

    content_type_ids = {link.from_content_type_id for link in links} | {
        link.to_content_type_id for link in links
    }
    labels = {
        ct.pk: f"{ct.app_label}.{ct.model}"
        for ct in ContentType.objects.filter(pk__in=content_type_ids)
    }

    index = _EdgeIndex(labels=labels)
    for link in links:
        from_key = (labels[link.from_content_type_id], link.from_object_id)
        to_key = (labels[link.to_content_type_id], link.to_object_id)
        if link.relation_type in REVERSE_IMPACT_RELATIONS:
            # 影響は「実装される側 → 実装する側」へ流れる。エッジ自体の向きは変えない。
            index.outgoing[to_key].append((link, from_key))
        else:
            index.outgoing[from_key].append((link, to_key))
    return index


def _edge_for(link: WorkLink, index: _EdgeIndex, nodes: dict) -> GraphEdge:
    from_key = (index.labels[link.from_content_type_id], link.from_object_id)
    to_key = (index.labels[link.to_content_type_id], link.to_object_id)
    return GraphEdge(
        id=link.pk,
        relation_type=link.relation_type,
        state=link.state,
        provenance=link.provenance,
        source_reference=link.source_reference,
        from_node=nodes.get(from_key, GraphNode(from_key[0], from_key[1], "")),
        to_node=nodes.get(to_key, GraphNode(to_key[0], to_key[1], "")),
        confirmed_by=str(link.confirmed_by) if link.confirmed_by_id else None,
        confirmed_at=link.confirmed_at,
    )


def _retitle(edge: GraphEdge, titled: dict) -> GraphEdge:
    return GraphEdge(
        id=edge.id,
        relation_type=edge.relation_type,
        state=edge.state,
        provenance=edge.provenance,
        source_reference=edge.source_reference,
        from_node=titled.get(edge.from_node.key, edge.from_node),
        to_node=titled.get(edge.to_node.key, edge.to_node),
        confirmed_by=edge.confirmed_by,
        confirmed_at=edge.confirmed_at,
    )


def _node_for(instance) -> GraphNode:
    meta = instance._meta
    return GraphNode(
        label=f"{meta.app_label}.{meta.model_name}",
        id=instance.pk,
        title=str(instance),
    )


def _with_titles(nodes: Iterable[GraphNode]) -> dict:
    """種別ごとにまとめて表示名を引く。ノード 1 件ごとに引かない。"""

    by_label: dict[str, list[UUID]] = defaultdict(list)
    for node in nodes:
        by_label[node.label].append(node.id)

    titles: dict[tuple[str, UUID], str] = {}
    for label, ids in by_label.items():
        app_label, model_name = label.split(".")
        # `get_by_natural_key` は ContentType のキャッシュを使う。
        # `get()` にすると種別ごとに毎回 1 クエリ増える。
        model = ContentType.objects.get_by_natural_key(app_label, model_name).model_class()
        if model is None:
            continue
        for obj in model.objects.filter(pk__in=ids):
            titles[(label, obj.pk)] = str(obj)

    return {
        node.key: GraphNode(label=node.label, id=node.id, title=titles.get(node.key, "（削除済み）"))
        for node in nodes
    }
