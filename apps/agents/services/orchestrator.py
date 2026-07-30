"""PMO Orchestrator。

仕様書 3 章の方針どおり、まず単一オーケストレーターを置き、既存の検索・評価機能を
ツールとして呼ぶ。マルチエージェント化は、この構造で必要性が明確になってから。

    意図分類 → 実行計画 → ツール実行 → 根拠評価 → 応答組み立て

各段階は必ず AgentRun / AgentStep として保存する（REQ-AG-008）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from apps.agents.models import AgentRun, AgentStep, EvidenceEvaluation
from apps.agents.services import intent as intent_service
from apps.agents.services.tools import registry
from apps.core.services.ai_settings import is_provider_configured


@dataclass
class Plan:
    """実行計画（REQ-AG-003）。"""

    search_required: bool
    tools: list[str]
    search_queries: list[str] = field(default_factory=list)
    expected_output: str = ""

    def as_dict(self) -> dict:
        return {
            "search_required": self.search_required,
            "tools": self.tools,
            "search_queries": self.search_queries,
            "expected_output": self.expected_output,
        }


@dataclass
class OrchestratorResult:
    run: AgentRun
    intent: intent_service.IntentResult
    plan: Plan
    hits: list
    evidence: EvidenceEvaluation | None


def build_plan(intent_result: intent_service.IntentResult, question: str) -> Plan:
    """意図から実行計画を組み立てる。

    LLM が使えない環境では LLM 必須ツールを計画に入れない。計画に入れておいて
    実行時に落とすより、最初から立てない方がトレースが読みやすい。
    """

    llm_enabled = is_provider_configured() and settings.AI_PROVIDER != "local_hash"
    available = set(registry.available(llm_enabled=llm_enabled))

    tools = [
        name
        for name in ("expand_query", "search_local_docs", "rerank_results", "evaluate_evidence")
        if name in available
    ]

    return Plan(
        search_required=True,
        tools=tools,
        search_queries=registry.get("expand_query").func(question, intent_result),
        expected_output=(
            f"{intent_result.label}について、"
            f"確認観点（{', '.join(intent_result.viewpoints[:3])}）に沿った整理"
        ),
    )


def run(
    *,
    tenant,
    question: str,
    area: str,
    index=None,
    user=None,
    project=None,
    top_k: int | None = None,
) -> OrchestratorResult:
    """オーケストレーターを 1 回実行する。

    ループ上限（NFR-AG-002）は `settings.AGENT["MAX_LOOPS"]`。現時点の実装は
    再検索ループを持たないため loop_count は常に 1。再検索を入れるときは、
    この関数の中でループし、必ず上限で打ち切ること。
    """

    started = timezone.now()
    intent_result = intent_service.classify(question)

    run_record = AgentRun.objects.create(
        tenant=tenant,
        project=project,
        user=user,
        area=area,
        user_input=question,
        intent=intent_result.intent,
        intent_confidence=intent_result.confidence,
        loop_count=1,
    )

    _record_step(
        run_record,
        order=1,
        tool_name="intent_classify",
        output_summary=f"{intent_result.label} (confidence={intent_result.confidence_label})",
    )

    plan = build_plan(intent_result, question)
    run_record.plan = plan.as_dict()
    run_record.save(update_fields=["plan", "updated_at"])

    _record_step(
        run_record,
        order=2,
        tool_name="build_plan",
        output_summary=f"tools={', '.join(plan.tools)}",
    )

    hits: list = []

    if index is not None and plan.search_required:
        hits = registry.get("search_local_docs").func(index, question, top_k=top_k)

    _record_step(
        run_record,
        order=3,
        tool_name="search_local_docs",
        status=AgentStep.Status.OK if index is not None else AgentStep.Status.SKIPPED,
        input_summary=question,
        output_summary=f"{len(hits)} 件取得" if index is not None else "インデックス未構築のためスキップ",
    )

    evidence_result = registry.get("evaluate_evidence").func(hits, intent_result)
    evidence = EvidenceEvaluation.objects.create(
        run=run_record,
        confidence=evidence_result.confidence,
        relevance=evidence_result.relevance,
        coverage=evidence_result.coverage,
        has_conflict=evidence_result.has_conflict,
        missing_information=evidence_result.missing_information,
        recommendation=evidence_result.recommendation,
        notes=evidence_result.notes,
    )

    _record_step(
        run_record,
        order=4,
        tool_name="evaluate_evidence",
        output_summary=f"{evidence.get_recommendation_display()} ({evidence.confidence:.2f})",
    )

    run_record.status = AgentRun.Status.SUCCEEDED
    run_record.elapsed_ms = int((timezone.now() - started).total_seconds() * 1000)
    run_record.save(update_fields=["status", "elapsed_ms", "updated_at"])

    return OrchestratorResult(
        run=run_record,
        intent=intent_result,
        plan=plan,
        hits=hits,
        evidence=evidence,
    )


def _record_step(
    run_record: AgentRun,
    *,
    order: int,
    tool_name: str,
    status: str = AgentStep.Status.OK,
    input_summary: str = "",
    output_summary: str = "",
) -> AgentStep:
    return AgentStep.objects.create(
        run=run_record,
        order=order,
        tool_name=tool_name,
        status=status,
        input_summary=input_summary[:500],
        output_summary=output_summary[:500],
    )
