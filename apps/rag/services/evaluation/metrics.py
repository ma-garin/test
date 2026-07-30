"""検索評価の指標計算。

副作用を持たない純関数だけを置く。手計算と突き合わせられることが、
評価基盤そのものを信用できるかどうかの前提になるため。

指標の定義（画面にも同じ文言を出す）:

- Recall@K   : 期待文書のうち、上位 K 件の検索結果に 1 つ以上のチャンクが
               現れたものの割合。質問ごとに算出し、その平均を取る。
- Precision@K: 上位 K 件の検索結果のうち、期待文書に属するものの割合。
               結果が K 件未満のときは分母を実際の取得件数にする。
- MRR        : 期待文書が最初に現れた順位の逆数（見つからなければ 0）の平均。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 期待文書も期待キーワードも無い Golden は採点できない。
NO_EXPECTATION = "期待する文書・キーワードが未設定のため採点できません"

#: Golden が 1 件も無いときの理由文。0 点ではなく「評価不能」と出す。
NO_GOLDEN = "有効な Golden 質問が 0 件のため評価できません（Recall は算出しません）"


@dataclass(frozen=True)
class CaseMetrics:
    """1 問分の採点結果。"""

    recall: float
    precision: float
    reciprocal_rank: float
    first_hit_rank: int | None
    matched: tuple[str, ...] = field(default_factory=tuple)
    missing: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SuiteMetrics:
    """スイート全体の集計。評価不能を 0 点と区別するため Optional にする。"""

    evaluable: bool
    case_count: int
    recall_at_k: float | None = None
    precision_at_k: float | None = None
    mrr: float | None = None
    reason: str = ""


def score_ranking(
    expected_ids: list[str],
    ranked_document_ids: list[str],
    *,
    top_k: int,
) -> CaseMetrics:
    """1 問分の Recall / Precision / 逆順位を求める。

    `ranked_document_ids` は上位から並んだ「検索結果チャンクの所属文書 ID」。
    同じ文書のチャンクが複数入りうるので、重複はそのまま Precision の分母に効く。
    """

    ranked = ranked_document_ids[:top_k]
    expected = list(dict.fromkeys(expected_ids))

    if not expected:
        return CaseMetrics(recall=0.0, precision=0.0, reciprocal_rank=0.0, first_hit_rank=None)

    expected_set = set(expected)
    matched = [document_id for document_id in expected if document_id in set(ranked)]
    missing = [document_id for document_id in expected if document_id not in set(ranked)]

    hit_positions = [
        position for position, document_id in enumerate(ranked, start=1) if document_id in expected_set
    ]
    first_hit_rank = hit_positions[0] if hit_positions else None

    return CaseMetrics(
        recall=len(matched) / len(expected),
        precision=(len(hit_positions) / len(ranked)) if ranked else 0.0,
        reciprocal_rank=(1.0 / first_hit_rank) if first_hit_rank else 0.0,
        first_hit_rank=first_hit_rank,
        matched=tuple(matched),
        missing=tuple(missing),
    )


def aggregate(case_metrics: list[CaseMetrics], *, reason: str = NO_GOLDEN) -> SuiteMetrics:
    """採点済みケースを平均する。0 件なら評価不能として返す。"""

    if not case_metrics:
        return SuiteMetrics(evaluable=False, case_count=0, reason=reason)

    count = len(case_metrics)

    return SuiteMetrics(
        evaluable=True,
        case_count=count,
        recall_at_k=sum(m.recall for m in case_metrics) / count,
        precision_at_k=sum(m.precision for m in case_metrics) / count,
        mrr=sum(m.reciprocal_rank for m in case_metrics) / count,
    )


def as_percent(value: float | None) -> float | None:
    """画面表示用の百分率。評価不能（None）はそのまま通す。"""

    return None if value is None else round(value * 100, 1)
