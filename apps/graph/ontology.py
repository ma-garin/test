"""GE-01: PMO 知識グラフのオントロジー。

「関係の型」を自由文にすると、影響範囲も遅延伝播も計算できない。ここで
許可された関係型と、その両端に置けるノード種別を宣言し、モデル側の検証で強制する。

不変条件（`docs/改善に.md` の「エッジの不変条件」）:
- `relation_type` は下表の型だけを使う。自由文の関係名を予測計算へ渡さない。
- すべての関係は有向。`A implements B` と `B implements A` は別物として扱う。
- 許可されていない両端の組み合わせは保存できない。
"""

from __future__ import annotations

from django.db import models


class RelationType(models.TextChoices):
    """許可された関係型。ここにない関係は保存できない。"""

    DEPENDS_ON = "depends_on", "依存する"
    BLOCKS = "blocks", "ブロックする"
    IMPACTS = "impacts", "影響する"
    IMPLEMENTS = "implements", "実装する"
    TESTS = "tests", "検証する"
    CAUSED_BY = "caused_by", "原因である"
    EVIDENCED_BY = "evidenced_by", "根拠づけられる"
    DISCUSSED_IN = "discussed_in", "議論されている"
    FORECASTS = "forecasts", "予測する"


class LinkState(models.TextChoices):
    """確認状態。`candidate` は表示・レビューできるが、予測の確定根拠には使わない。"""

    CONFIRMED = "confirmed", "確定"
    CANDIDATE = "candidate", "候補"
    REJECTED = "rejected", "否定"
    OBSOLETE = "obsolete", "失効"


class Provenance(models.TextChoices):
    """出所。これを持たないエッジは、PMO の判断根拠に使えない。"""

    EXTERNAL_ID = "external_id", "外部ID一致"
    MANUAL = "manual", "手動登録"
    RULE = "rule", "設定済み規則"
    SIGNAL = "signal", "外部Signal"
    DOCUMENT = "document", "文書"
    AI_CANDIDATE = "ai_candidate", "AI候補"


#: 予測の確定根拠に使ってよい状態。AI 候補・未確認は含めない。
CONFIRMED_STATES = (LinkState.CONFIRMED,)

#: 人の確認なしに `confirmed` にしてよい出所。ID 一致と手動登録だけを自動確定できる。
AUTO_CONFIRMABLE = (Provenance.EXTERNAL_ID, Provenance.MANUAL)

#: 関係型ごとに許可する `(始点モデル, 終点モデル)`。`app_label.modelname` は小文字。
#: 新しいノード種別（Signal、TestEvidence 等）を足すときは、ここへ追記して初めて使える。
ALLOWED_ENDPOINTS: dict[str, tuple[tuple[str, str], ...]] = {
    RelationType.IMPLEMENTS: (
        ("graph.component", "graph.feature"),
        ("projects.wbstask", "graph.feature"),
    ),
    RelationType.IMPACTS: (
        ("projects.defect", "graph.feature"),
        ("projects.defect", "graph.component"),
        ("projects.defect", "projects.wbstask"),
        ("projects.issue", "graph.feature"),
        ("projects.issue", "projects.wbstask"),
        ("projects.changerequest", "graph.feature"),
        ("projects.changerequest", "projects.wbstask"),
        ("projects.risk", "graph.feature"),
    ),
    RelationType.BLOCKS: (
        ("projects.defect", "projects.wbstask"),
        ("projects.issue", "projects.wbstask"),
        ("projects.defect", "graph.feature"),
    ),
    RelationType.DEPENDS_ON: (
        ("graph.feature", "graph.feature"),
        ("graph.component", "graph.component"),
    ),
    RelationType.TESTS: (
        ("graph.component", "graph.feature"),
        ("projects.qualitymetric", "graph.feature"),
    ),
    RelationType.CAUSED_BY: (
        ("projects.defect", "projects.changerequest"),
        ("projects.issue", "projects.risk"),
    ),
    RelationType.EVIDENCED_BY: (
        ("graph.feature", "documents.document"),
        ("projects.defect", "documents.document"),
    ),
    RelationType.DISCUSSED_IN: (
        ("graph.feature", "documents.document"),
        ("projects.defect", "documents.document"),
    ),
    RelationType.FORECASTS: (),
}


def endpoint_label(instance_or_meta) -> str:
    """モデル実体またはメタから `app_label.modelname` を作る。"""

    meta = getattr(instance_or_meta, "_meta", instance_or_meta)
    return f"{meta.app_label}.{meta.model_name}"


def is_allowed(relation_type: str, from_label: str, to_label: str) -> bool:
    """その関係型に、その両端の組み合わせを許可しているか。"""

    return (from_label, to_label) in ALLOWED_ENDPOINTS.get(relation_type, ())


def allowed_targets(relation_type: str, from_label: str) -> tuple[str, ...]:
    """始点から張れる終点の一覧。画面で選択肢を出すときに使う。"""

    return tuple(
        to_label
        for source, to_label in ALLOWED_ENDPOINTS.get(relation_type, ())
        if source == from_label
    )


def register_endpoints(relation_type: str, pairs: tuple[tuple[str, str], ...]) -> None:
    """後続チケット（Signal・TestEvidence 等）が両端を追加するための入口。

    オントロジーを各所で書き換えられるようにすると、関係型の意味がずれる。
    追加はこの関数を通し、既存の組み合わせは消さない（追記のみ）。
    """

    current = ALLOWED_ENDPOINTS.get(relation_type, ())
    ALLOWED_ENDPOINTS[relation_type] = current + tuple(p for p in pairs if p not in current)
