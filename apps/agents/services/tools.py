"""ツールレジストリ（仕様書 6 章 Tool Registry）。

オーケストレーターは既存機能を「ツール」として呼ぶ。ツールを増やすときは
`register()` するだけで、プランナー側の分岐を増やさない。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ToolFunc = Callable[..., Any]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    func: ToolFunc
    requires_llm: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, name: str, description: str, *, requires_llm: bool = False):
        def decorator(func: ToolFunc) -> ToolFunc:
            self._tools[name] = Tool(
                name=name,
                description=description,
                func=func,
                requires_llm=requires_llm,
            )

            return func

        return decorator

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"未登録のツールです: {name}")

        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def available(self, *, llm_enabled: bool) -> list[str]:
        """LLM が使えない環境では、LLM 必須ツールを計画から外す。"""

        return sorted(
            name for name, tool in self._tools.items() if llm_enabled or not tool.requires_llm
        )


registry = ToolRegistry()


@registry.register("expand_query", "意図から検索クエリを補強する")
def expand_query(question: str, intent_result) -> list[str]:
    """検索クエリを拡張する。

    LLM を使わず、意図分類で得た定型語を足すだけの実装。旧実装の
    `generate_retrieval_queries()` を LLM ありに差し替える場合もここを置き換える。
    """

    base = str(question or "").strip()

    if not base:
        return []

    return [base] + [f"{base} {term}" for term in intent_result.retrieval_terms[:3]]


@registry.register("search_local_docs", "登録文書をハイブリッド検索する")
def search_local_docs(index, question: str, *, top_k: int | None = None):
    from apps.rag.services.retriever import search

    return search(index, question, top_k=top_k)


@registry.register("get_pmo_viewpoints", "意図に応じた確認観点を返す")
def get_pmo_viewpoints(intent_result) -> list[str]:
    return list(intent_result.viewpoints)


@registry.register("evaluate_evidence", "取得根拠の十分性を評価する")
def evaluate_evidence(hits, intent_result):
    from apps.agents.services.evidence import evaluate

    return evaluate(hits, intent_result)


@registry.register("answer_question", "根拠から回答本文を組み立てる")
def answer_question(*, question, hits, evidence, intent_result, project_context=None):
    """回答生成の第 1 層（ADR-0004）。

    `requires_llm=False`。LLM が無くても必ず動く方を主とし、
    文体整形（`polish`）は上乗せに留める。出所を持てない主張は
    そもそも組み立てられない構造なので、事実誤認が入り込む余地が無い。
    """

    from apps.rag.services.answer import assemble

    return assemble(
        question=question,
        hits=hits,
        evidence=evidence,
        intent_result=intent_result,
        project_context=project_context,
    )


@registry.register("rerank_results", "LLM で検索結果を再順位付けする", requires_llm=True)
def rerank_results(hits, question: str):
    """LLM リランク。

    未実装。`apps.rag.services.retriever` の結果を受け取り、順位を入れ替えて返す。
    実装するまでは計画に含まれても呼ばれないよう、プランナー側で除外している。
    """

    raise NotImplementedError("LLM リランクは未実装です")


@registry.register("draft_deliverable", "成果物ドラフトを生成する", requires_llm=True)
def draft_deliverable(context: dict[str, Any]):
    raise NotImplementedError("成果物ドラフト生成は未実装です")
