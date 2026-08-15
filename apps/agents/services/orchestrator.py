"""PMO Orchestrator。

仕様書 3 章の方針どおり、まず単一オーケストレーターを置き、既存の検索・評価機能を
ツールとして呼ぶ。マルチエージェント化は、この構造で必要性が明確になってから。

    意図分類 → 実行計画 → ツール実行 → 根拠評価 → 応答組み立て

各段階は必ず AgentRun / AgentStep として保存する（REQ-AG-008）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils import timezone

from apps.agents.models import AgentRun, AgentStep, EvidenceEvaluation
from apps.agents.services import intent as intent_service
from apps.agents.services.screen_context import ScreenContext
from apps.agents.services.tools import registry
from apps.core.services.ai_settings import effective_config


@dataclass
class Plan:
    """実行計画（REQ-AG-003）。"""

    search_required: bool
    tools: list[str]
    search_queries: list[str] = field(default_factory=list)
    expected_output: str = ""
    #: 開いていた画面の文脈（あれば）。トレースから「何を見ながらの相談か」を復元する。
    screen_context: dict | None = None

    def as_dict(self) -> dict:
        return {
            "search_required": self.search_required,
            "tools": self.tools,
            "search_queries": self.search_queries,
            "expected_output": self.expected_output,
            "screen_context": self.screen_context,
        }


@dataclass
class OrchestratorResult:
    run: AgentRun
    intent: intent_service.IntentResult
    plan: Plan
    hits: list
    evidence: EvidenceEvaluation | None
    screen_context: ScreenContext | None = None


def build_plan(
    intent_result: intent_service.IntentResult,
    question: str,
    screen_context: ScreenContext | None = None,
) -> Plan:
    """意図から実行計画を組み立てる。

    LLM が使えない環境では LLM 必須ツールを計画に入れない。計画に入れておいて
    実行時に落とすより、最初から立てない方がトレースが読みやすい。
    """

    # 利用者ごとの API 設定を見る。管理者が設定していなくても、自分のキーを
    # 入れた利用者には LLM 必須ツールを使わせる。
    ai_config = effective_config()
    llm_enabled = ai_config.is_configured and ai_config.provider != "local_hash"
    available = set(registry.available(llm_enabled=llm_enabled))

    tools = [
        name
        for name in ("expand_query", "search_local_docs", "rerank_results", "evaluate_evidence")
        if name in available
    ]

    # 画面文脈があれば確認観点の先頭へ足す。画面固有の観点（リスク画面なら
    # 「対策の有無」「期限」）は意図分類だけでは出てこないため。
    viewpoints = list(intent_result.viewpoints)

    if screen_context is not None:
        viewpoints = list(screen_context.viewpoints) + [
            v for v in viewpoints if v not in screen_context.viewpoints
        ]

    subject = f"{screen_context.headline}、" if screen_context is not None else ""

    return Plan(
        search_required=True,
        tools=tools,
        search_queries=registry.get("expand_query").func(question, intent_result),
        expected_output=(
            f"{subject}{intent_result.label}について、"
            f"確認観点（{', '.join(viewpoints[:3])}）に沿った整理"
        ),
        screen_context=screen_context.as_dict() if screen_context is not None else None,
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
    screen_context: ScreenContext | None = None,
) -> OrchestratorResult:
    """オーケストレーターを 1 回実行する。

    ループ上限（NFR-AG-002）は `settings.AGENT["MAX_LOOPS"]`。現時点の実装は
    再検索ループを持たないため loop_count は常に 1。再検索を入れるときは、
    この関数の中でループし、必ず上限で打ち切ること。
    """

    started = timezone.now()
    # 意図分類は利用者が書いた文だけで行う。画面文脈を混ぜると、画面名の語
    # （「リスク」「品質」）で分類が引っ張られ、相談内容と食い違う。
    intent_result = intent_service.classify(question)
    # 保存する入力には画面文脈を含める。後からトレースを見た人が
    # 「何を見ながらの相談か」を復元できないと、判断の妥当性を検証できない。
    user_input = screen_context.decorate(question) if screen_context is not None else question

    run_record = AgentRun.objects.create(
        tenant=tenant,
        project=project,
        user=user,
        area=area,
        user_input=user_input,
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

    plan = build_plan(intent_result, question, screen_context)
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
        screen_context=screen_context,
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
