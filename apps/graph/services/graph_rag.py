"""GE-06: 確定リンクだけを使う GraphRAG（多段根拠検索）の試作。

ベクトル検索だけの回答は「似ている文書」を並べられるが、なぜその文書が根拠なのかを
説明できない。ここでは質問文から起点の実体を拾い、**確定済みの関連だけ**をたどって
文書・Signal まで到達する経路を作り、経路の各エッジの関係型・出所・確認者を添えて返す。

守る条件（`docs/改善に.md` GE-06）:
- 候補（`candidate`）リンクは根拠にしない。`build_impact(include_candidates=False)` を使う。
- 案件・テナントの境界を越えない。終点は起点と同じ案件のものだけを採る。
- 鮮度を落とさない。`Signal` は `is_usable_as_evidence` のものだけ。鮮度切れは結果に残す。
- 出所（`provenance`）と確認者（`confirmed_by`）を経路のエッジごとに持つ。
- 外部ネットワークへ出ない。LLM も呼ばない。探索は決定論的に行う。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.contrib.contenttypes.models import ContentType

from apps.documents.models import Document
from apps.forecast.models.signals import Signal
from apps.forecast.services.freshness import ProjectFreshness
from apps.forecast.services.linking import (
    KEY_PATTERN,
    MIN_FEATURE_NAME_LENGTH,
    WBS_PATTERN,
)
from apps.graph.models.graph import Feature, WorkLink
from apps.graph.ontology import LinkState, RelationType
from apps.graph.services.queries import (
    MAX_IMPACT_DEPTH,
    REVERSE_IMPACT_RELATIONS,
    GraphEdge,
    GraphNode,
    ImpactResult,
    build_impact,
)
from apps.projects.models import Defect, Issue, WbsTask

#: 根拠へ渡る最後の 1 ホップに使う関係型。影響の伝播とは別に扱う。
EVIDENCE_RELATIONS = (RelationType.EVIDENCED_BY, RelationType.DISCUSSED_IN)

#: 根拠として返してよい終点の種別。ここにないノードは経路の途中にしかならない。
EVIDENCE_LABELS = ("documents.document", "forecast.signal")

#: 外部キーで起点を引くモデル。`external_key` を持たないモデルは自動的に飛ばす。
EXTERNAL_KEY_MODELS = (Issue, Defect)

#: 1 回の質問で返す経路の上限。超えたら `truncated` を立て、黙って切らない。
MAX_EVIDENCE_PATHS = 50


@dataclass(frozen=True)
class EvidencePath:
    """起点から根拠 1 件までの経路。「なぜこれが根拠か」を経路そのもので説明する。"""

    origin: GraphNode
    evidence: GraphNode
    edges: tuple[GraphEdge, ...]
    #: ベクトル検索でも同じ文書が出ていたか。グラフ固有の説明力を測るための印。
    found_by_vector: bool = False
    #: 根拠が鮮度切れの情報源に属する場合、その情報源。呼び出し側が古さを表示できる。
    stale_source: str | None = None

    @property
    def is_graph_only(self) -> bool:
        """ベクトル検索では出ず、確定リンクの経路でのみ説明できる根拠か。"""

        return not self.found_by_vector

    @property
    def hops(self) -> int:
        return len(self.edges)

    def explain(self) -> str:
        """画面・通知にそのまま出せる説明文。内部キーを出さない。"""

        parts = [self.origin.title or self.origin.label]
        for edge in self.edges:
            target = edge.to_node if edge.relation_type not in REVERSE_IMPACT_RELATIONS else edge.from_node
            confirmer = edge.confirmed_by or "未記録"
            parts.append(
                f"-[{edge.relation_label} / 出所:{edge.provenance_label} / 確認:{confirmer}]->"
                f" {target.title or target.label}"
            )
        text = " ".join(parts)
        if self.stale_source:
            text += "（鮮度切れの情報源です）"
        return text


@dataclass(frozen=True)
class GraphRagResult:
    """質問 1 件分の根拠検索結果。空でも例外にせず、空のまま返す。"""

    question: str
    origins: tuple[GraphNode, ...] = ()
    paths: tuple[EvidencePath, ...] = ()
    #: ベクトル検索では出たが、確定リンクの経路では説明できなかった文書 ID。
    vector_only_document_ids: tuple[str, ...] = ()
    freshness: ProjectFreshness | None = None
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.paths

    @property
    def graph_only_paths(self) -> tuple[EvidencePath, ...]:
        return tuple(path for path in self.paths if path.is_graph_only)

    @property
    def overlapping_paths(self) -> tuple[EvidencePath, ...]:
        return tuple(path for path in self.paths if path.found_by_vector)

    @property
    def stale_paths(self) -> tuple[EvidencePath, ...]:
        return tuple(path for path in self.paths if path.stale_source)

    @property
    def graph_only_count(self) -> int:
        """グラフ経由でしか説明できない根拠の件数（終点の実体数で数える）。"""

        return len({path.evidence.key for path in self.graph_only_paths})

    @property
    def vector_only_count(self) -> int:
        return len(self.vector_only_document_ids)

    def describe(self) -> str:
        if self.is_empty:
            return "質問から起点になる実体を特定できないか、確定した関連が 1 本もありません。"
        return (
            f"確定リンクの経路で {len(self.paths)} 件の根拠を説明できます"
            f"（うちグラフ経由のみ {self.graph_only_count} 件 / "
            f"ベクトル検索のみ {self.vector_only_count} 件）。"
        )


def explain_with_graph(
    project,
    question: str,
    *,
    vector_hits: Iterable = (),
    max_depth: int = MAX_IMPACT_DEPTH,
    now: datetime | None = None,
) -> GraphRagResult:
    """質問から起点を拾い、確定リンクだけをたどって根拠までの経路を返す。

    `vector_hits` には既存のベクトル検索が返した文書 ID を渡す。グラフ経由でしか
    説明できない根拠と、ベクトル検索だけで出た根拠を区別して返すため。
    """

    freshness = ProjectFreshness.for_project(project, now=now)
    origins = _resolve_origins(project, question or "")
    if not origins:
        return GraphRagResult(question=question, freshness=freshness)

    vector_ids = {str(item) for item in vector_hits}
    stale_sources = {item.source for item in freshness.stale_sources}

    routes: list[tuple[GraphNode, dict[tuple[str, UUID], tuple[GraphEdge, ...]], dict]] = []
    reachable: set[tuple[str, UUID]] = set()
    truncated = False
    for origin in origins:
        impact = build_impact(origin, include_candidates=False, max_depth=max_depth)
        truncated = truncated or impact.truncated
        node_routes, nodes = _routes_from(impact)
        routes.append((impact.origin, node_routes, nodes))
        reachable |= set(node_routes)

    evidence_links = _evidence_links(project, reachable)
    targets, signal_sources = _resolve_targets(project, evidence_links)

    paths: list[EvidencePath] = []
    seen: set[tuple[tuple[str, UUID], tuple[str, UUID], UUID]] = set()
    for origin_node, node_routes, nodes in routes:
        for from_key, link, to_key in evidence_links:
            evidence_node = targets.get(to_key)
            if evidence_node is None or from_key not in node_routes:
                continue
            marker = (origin_node.key, to_key, link.pk)
            if marker in seen:
                continue
            if len(paths) >= MAX_EVIDENCE_PATHS:
                truncated = True
                break
            seen.add(marker)
            source_node = nodes.get(from_key, GraphNode(from_key[0], from_key[1], ""))
            paths.append(
                EvidencePath(
                    origin=origin_node,
                    evidence=evidence_node,
                    edges=node_routes[from_key] + (_edge(link, source_node, evidence_node),),
                    found_by_vector=(
                        to_key[0] == "documents.document" and str(to_key[1]) in vector_ids
                    ),
                    stale_source=_stale_source(signal_sources.get(to_key), stale_sources),
                )
            )
        if truncated and len(paths) >= MAX_EVIDENCE_PATHS:
            break

    graph_document_ids = {
        str(path.evidence.id) for path in paths if path.evidence.label == "documents.document"
    }
    return GraphRagResult(
        question=question,
        origins=tuple(origin for origin, _, _ in routes),
        paths=tuple(paths),
        vector_only_document_ids=tuple(sorted(vector_ids - graph_document_ids)),
        freshness=freshness,
        truncated=truncated,
    )


# ── 起点の特定 ─────────────────────────────────────────────


def _resolve_origins(project, question: str) -> tuple:
    """質問文から起点を拾う。`linking.py` と同じ優先順位（上位が出たら下位は試さない）。

    1. 明示的な外部キー・WBS コード … 誤りにくいのでこれだけで足りる
    2. 機能名の一致            … 誤検出しやすいので、上位が空のときだけ使う
    """

    explicit = _by_external_key(project, question) + _by_wbs_code(project, question)
    if explicit:
        return explicit
    return _by_feature_name(project, question)


def _by_external_key(project, text: str) -> tuple:
    keys = set(KEY_PATTERN.findall(text))
    if not keys:
        return ()

    found: list = []
    for model in EXTERNAL_KEY_MODELS:
        if not _has_field(model, "external_key"):
            continue
        found.extend(model.objects.filter(project=project, external_key__in=keys))
    return tuple(found)


def _by_wbs_code(project, text: str) -> tuple:
    codes = set(WBS_PATTERN.findall(text))
    if not codes:
        return ()
    return tuple(WbsTask.objects.filter(project=project, wbs_code__in=codes))


def _by_feature_name(project, text: str) -> tuple:
    return tuple(
        feature
        for feature in Feature.objects.filter(project=project)
        if len(feature.name) >= MIN_FEATURE_NAME_LENGTH and feature.name in text
    )


def _has_field(model, name: str) -> bool:
    return any(field.name == name for field in model._meta.get_fields())


# ── 経路の組み立て ──────────────────────────────────────────


def _routes_from(impact: ImpactResult) -> tuple[dict, dict]:
    """影響探索の結果から「起点 → 各ノード」の経路（エッジ列）を作る。

    `build_impact` は到達したエッジを平らに返すため、経路として説明するには
    たどった向きで並べ直す必要がある。`implements` は影響の向きが逆になる。
    """

    nodes = {node.key: node for node in impact.nodes}
    adjacency: dict[tuple[str, UUID], list[tuple[GraphNode, GraphEdge]]] = defaultdict(list)
    for edge in impact.edges:
        if edge.relation_type in REVERSE_IMPACT_RELATIONS:
            adjacency[edge.to_node.key].append((edge.from_node, edge))
        else:
            adjacency[edge.from_node.key].append((edge.to_node, edge))

    routes: dict[tuple[str, UUID], tuple[GraphEdge, ...]] = {impact.origin.key: ()}
    frontier = [impact.origin]
    while frontier:
        next_frontier: list[GraphNode] = []
        for node in frontier:
            for target, edge in adjacency.get(node.key, ()):
                if target.key in routes:
                    continue
                routes[target.key] = routes[node.key] + (edge,)
                next_frontier.append(target)
        frontier = next_frontier
    return routes, nodes


def _evidence_links(project, reachable: set) -> tuple:
    """到達済みノードから伸びる、確定済みの根拠エッジを 1 回の問い合わせで読む。"""

    if not reachable:
        return ()

    links = list(
        WorkLink.objects.filter(
            project=project,
            relation_type__in=EVIDENCE_RELATIONS,
            state=LinkState.CONFIRMED,
        ).select_related("confirmed_by")
    )
    if not links:
        return ()

    content_type_ids = {link.from_content_type_id for link in links} | {
        link.to_content_type_id for link in links
    }
    labels = {
        ct.pk: f"{ct.app_label}.{ct.model}"
        for ct in ContentType.objects.filter(pk__in=content_type_ids)
    }

    found = []
    for link in links:
        from_key = (labels[link.from_content_type_id], link.from_object_id)
        to_key = (labels[link.to_content_type_id], link.to_object_id)
        if to_key[0] not in EVIDENCE_LABELS or from_key not in reachable:
            continue
        found.append((from_key, link, to_key))
    return tuple(found)


def _resolve_targets(project, evidence_links: Sequence) -> tuple[dict, dict]:
    """終点の実体を案件で絞って引く。案件外・無効化された根拠はここで落とす。

    返すのは `(ノード, Signal の情報源)` の 2 つ。情報源は鮮度切れの判定に使う。
    """

    by_label: dict[str, list[UUID]] = defaultdict(list)
    for _, _, to_key in evidence_links:
        by_label[to_key[0]].append(to_key[1])

    nodes: dict[tuple[str, UUID], GraphNode] = {}
    signal_sources: dict[tuple[str, UUID], str] = {}
    for document in Document.objects.filter(
        project=project, pk__in=by_label.get("documents.document", ())
    ):
        nodes[("documents.document", document.pk)] = GraphNode(
            label="documents.document", id=document.pk, title=str(document)
        )
    for signal in Signal.objects.filter(project=project, pk__in=by_label.get("forecast.signal", ())):
        if not signal.is_usable_as_evidence:
            # 無効化・訂正済みの Signal を根拠に使うと、訂正前の事実で説明してしまう。
            continue
        key = ("forecast.signal", signal.pk)
        nodes[key] = GraphNode(label="forecast.signal", id=signal.pk, title=str(signal))
        signal_sources[key] = signal.source
    return nodes, signal_sources


def _stale_source(source: str | None, stale_sources: set) -> str | None:
    return source if source and source in stale_sources else None


def _edge(link: WorkLink, from_node: GraphNode, to_node: GraphNode) -> GraphEdge:
    return GraphEdge(
        id=link.pk,
        relation_type=link.relation_type,
        state=link.state,
        provenance=link.provenance,
        source_reference=link.source_reference,
        from_node=from_node,
        to_node=to_node,
        confirmed_by=str(link.confirmed_by) if link.confirmed_by_id else None,
        confirmed_at=link.confirmed_at,
    )
