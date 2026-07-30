"""回答評価 dry-run（traceability #70）。

生成物を保存せずに `chat.build_reply()` だけを回し、次の 3 点を確認する。

1. 引用（根拠）が付いているか
2. 必須セクションが本文に含まれるか
3. 根拠不足のときに断定せず抑制できているか

ADR-0003 のとおり LLM は呼ばない。ルールベース応答の劣化検知が目的。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.rag.models import GoldenQuestion, VectorIndex
from apps.rag.services import chat
from apps.rag.services.retriever import search


@dataclass(frozen=True)
class AnswerCaseResult:
    golden: GoldenQuestion
    passed: bool
    issues: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def evaluate_case(index: VectorIndex | None, question: GoldenQuestion, *, top_k: int) -> AnswerCaseResult:
    """Golden 1 件について回答の形式的な健全性を確認する（保存しない）。"""

    hits = search(index, question.question, top_k=top_k) if index is not None else []
    reply = chat.build_reply(question.question, hits)
    issues: list[str] = []

    has_citation = bool(reply.citations)
    abstained = reply.needs_clarification
    missing_sections = [
        section for section in (question.required_sections or []) if section not in reply.body
    ]

    if question.must_abstain and not abstained:
        issues.append("根拠不足でも回答を抑制していません（断定のリスク）")

    if not hits and not abstained:
        issues.append("検索結果が 0 件なのに確認を促していません")

    if not question.must_abstain and hits and not has_citation:
        issues.append("検索結果があるのに引用が付いていません")

    if missing_sections:
        issues.append(f"回答に必須セクションが欠けています: {' / '.join(missing_sections)}")

    if index is None:
        issues.append("検索インデックスが未構築のため、抑制の確認しかできていません")

    return AnswerCaseResult(
        golden=question,
        passed=not issues,
        issues=issues,
        detail={
            "citations": len(reply.citations),
            "abstained": abstained,
            "recommendation": reply.recommendation_label,
            "retrieved": len(hits),
            "missing_sections": missing_sections,
        },
    )


def evaluate_all(
    index: VectorIndex | None,
    questions: list[GoldenQuestion],
    *,
    top_k: int,
) -> list[AnswerCaseResult]:
    return [evaluate_case(index, question, top_k=top_k) for question in questions]
