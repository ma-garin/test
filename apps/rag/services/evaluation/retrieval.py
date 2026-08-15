"""検索評価（traceability #68 / #69）。

#68 Golden Dataset 評価  : 期待する参照元が検索結果に含まれるかを測る。
#69 APIなし検索評価      : `use_vector=False` で Embedding を一切呼ばず、
                          既存インデックスの語彙検索だけで Top-K を採点する。

外部 API は呼ばない。既定の `local_hash` でも語彙のみでも同じ経路を通る。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.rag.models import GoldenQuestion, VectorIndex
from apps.rag.services.evaluation import golden as golden_service
from apps.rag.services.evaluation.metrics import CaseMetrics, score_ranking
from apps.rag.services.retriever import search

#: インデックスが無いときの理由。0 点ではなく評価不能として扱う。
NO_INDEX = "検索インデックスが未構築のため評価できません"


@dataclass(frozen=True)
class CaseResult:
    """1 問分の評価結果。保存前の中間表現。"""

    golden: GoldenQuestion
    metrics: CaseMetrics | None
    evaluable: bool
    passed: bool
    issues: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def _term_coverage(terms: list[str], hits) -> tuple[float, list[str]]:
    """期待キーワードが検索結果の本文に現れた割合と、現れなかった語。"""

    if not terms:
        return 1.0, []

    corpus = "\n".join(hit.chunk.text for hit in hits)
    missing = [term for term in terms if term and term not in corpus]

    return (len(terms) - len(missing)) / len(terms), missing


def evaluate_case(
    index: VectorIndex | None,
    question: GoldenQuestion,
    *,
    top_k: int,
    use_vector: bool,
) -> CaseResult:
    """Golden 1 件を採点する。期待文書の欠損は黙って捨てず issues に残す。"""

    expected = golden_service.expected_documents_of(question)
    issues = golden_service.integrity_issues(question, expected)
    expected_ids = golden_service.available_expected_ids(expected)

    if index is None:
        return CaseResult(
            golden=question,
            metrics=None,
            evaluable=False,
            passed=False,
            issues=[*issues, NO_INDEX],
        )

    hits = search(index, question.question, top_k=top_k, use_vector=use_vector)
    # 業務データ由来のチャンクは文書に紐づかない（`document` が None）。
    # そのまま並べると "None" が順位に混ざり、Precision の分母を膨らませて
    # 指標を実際より悪く見せる。採点は文書由来のヒットだけで行う。
    ranked_ids = [str(hit.chunk.document_id) for hit in hits if hit.chunk.document_id]
    coverage, missing_terms = _term_coverage(list(question.expected_terms or []), hits)
    detail = {
        "retrieved": len(hits),
        "term_coverage": round(coverage, 4),
        "missing_terms": missing_terms,
        "use_vector": use_vector,
        "top_documents": list(
            dict.fromkeys(hit.chunk.document.title for hit in hits if hit.chunk.document_id)
        )[:5],
        # 文書に紐づかないヒットの数。指標に入らないぶん、見えるようにしておく。
        "business_hits": sum(1 for hit in hits if not hit.chunk.document_id),
    }

    if not expected_ids:
        # 期待文書が無い（未設定 or 全滅）ときに Recall を出すと嘘になる。
        return CaseResult(
            golden=question,
            metrics=None,
            evaluable=False,
            passed=False,
            issues=[*issues, "採点可能な期待文書が無いため Recall/MRR を算出しません"],
            detail=detail,
        )

    metrics = score_ranking(expected_ids, ranked_ids, top_k=top_k)
    titles = {str(item.document_id): item.title for item in expected}
    detail["matched_titles"] = [titles.get(doc_id, doc_id) for doc_id in metrics.matched]
    detail["missing_titles"] = [titles.get(doc_id, doc_id) for doc_id in metrics.missing]

    if metrics.missing:
        issues.append(
            "上位{k}件に出なかった期待文書: {titles}".format(
                k=top_k, titles=" / ".join(detail["missing_titles"])
            )
        )

    if missing_terms:
        issues.append(f"検索結果に現れなかった期待キーワード: {' / '.join(missing_terms)}")

    return CaseResult(
        golden=question,
        metrics=metrics,
        evaluable=True,
        passed=metrics.recall >= 1.0 and not issues,
        issues=issues,
        detail=detail,
    )


def evaluate_all(
    index: VectorIndex | None,
    questions: list[GoldenQuestion],
    *,
    top_k: int,
    use_vector: bool,
) -> list[CaseResult]:
    return [
        evaluate_case(index, question, top_k=top_k, use_vector=use_vector) for question in questions
    ]
