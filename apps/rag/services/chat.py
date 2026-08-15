"""チャットモードの応答生成。

ADR-0003 の「ルールベース優先」に従い、ここでは LLM を呼ばない。
検索（`apps.rag.services.retriever`）と根拠評価（`apps.agents.services.evidence`）
の結果だけで応答を組み立てる。

根拠が不足している（`ASK_CLARIFICATION`）ときに断定した文面を返さないことは、
この画面の必須要件。文面の分岐は `_build_body()` に集約し、他所で組み立てない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.agents.models import Recommendation
from apps.agents.services import evidence as evidence_service
from apps.agents.services import intent as intent_service
from apps.core.services.ai_settings import effective_config
from apps.rag.models import (
    AnswerCitation,
    ChatMessage,
    ChatSession,
    RagAnswer,
    RetrievalQuery,
    RetrievedChunk,
)
from apps.rag.services import project_context as project_context_service
from apps.rag.services.retriever import SearchHit, search

#: 引用として画面へ出す本文の長さ。長すぎると根拠ではなく本文の再掲になる。
QUOTE_LENGTH = 160

#: 応答に添える引用の最大件数。
MAX_CITATIONS = 3

#: ルールベース応答であることを記録に残すためのモデル名。
RULE_BASED_MODEL = "rule-based-v1"


@dataclass(frozen=True)
class Citation:
    rank: int
    title: str
    page: int
    quote: str
    score: float


@dataclass
class ChatReply:
    body: str
    recommendation: str
    confidence: float
    intent_label: str
    citations: list[Citation] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)

    @property
    def needs_clarification(self) -> bool:
        return self.recommendation == Recommendation.ASK_CLARIFICATION

    @property
    def recommendation_label(self) -> str:
        return Recommendation(self.recommendation).label

    @property
    def tone(self) -> str:
        """根拠の十分性の色分け。r=確認要 / a=注意付き / g=回答可。"""

        if self.needs_clarification:
            return "r"

        return "a" if self.recommendation == Recommendation.ANSWER_WITH_CAUTION else "g"


def _citations(hits: list[SearchHit]) -> list[Citation]:
    return [
        Citation(
            rank=hit.rank,
            title=hit.chunk.source_title,
            page=hit.chunk.page_number,
            quote=hit.chunk.text[:QUOTE_LENGTH],
            score=hit.final_score,
        )
        for hit in hits[:MAX_CITATIONS]
    ]


def _build_body(intent_result, evidence, citations: list[Citation]) -> str:
    """応答本文を組み立てる。根拠不足なら回答せず確認を促す。"""

    viewpoints = " / ".join(intent_result.viewpoints[:4])

    if evidence.recommendation == Recommendation.ASK_CLARIFICATION:
        lines = [
            "登録文書からご質問の根拠を見つけられませんでした。",
            "誤った断定を避けるため、回答の前に次の点を確認させてください。",
        ]
        lines += [f"・{item}" for item in evidence.missing_information]
        lines.append(f"（{intent_result.label}として整理する場合の観点: {viewpoints}）")

        return "\n".join(lines)

    lines = [f"{intent_result.label}として整理しました。登録文書から確認できたことは次のとおりです。"]
    lines += [f"・{c.quote}（{c.title} p.{c.page}）" for c in citations]

    if evidence.recommendation == Recommendation.ANSWER_WITH_CAUTION:
        lines.append("ただし根拠が限定的です。以下を確認したうえでご判断ください。")
        lines += [f"・{item}" for item in evidence.missing_information]

    lines.append(f"確認観点: {viewpoints}")

    return "\n".join(lines)


def build_reply(question: str, hits: list[SearchHit], context=None) -> ChatReply:
    """検索結果から応答を組み立てる。保存はしない（テストしやすさのため分離）。

    `context`（案件の現在値）があれば応答の冒頭へ付ける。登録文書には書かれていない
    進捗・課題数・期限超過は DB にしかなく、これが無いと「この案件の課題は？」に
    答えられないため。
    """

    intent_result = intent_service.classify(question)
    evidence = evidence_service.evaluate(hits, intent_result)
    citations = _citations(hits)
    body = _build_body(intent_result, evidence, citations)

    if context is not None:
        body = f"{context.as_text()}\n\n{body}"

    return ChatReply(
        body=body,
        recommendation=evidence.recommendation,
        confidence=evidence.confidence,
        intent_label=intent_result.label,
        citations=citations,
        missing_information=list(evidence.missing_information),
        follow_up_questions=list(intent_result.viewpoints[:4]),
    )


def _persist_answer(session: ChatSession, question: str, hits: list[SearchHit], reply: ChatReply):
    """検索と回答を保存する。根拠の追跡は監査要件なので必ず残す。"""

    ai_config = effective_config()
    query = RetrievalQuery.objects.create(
        tenant=session.tenant,
        project=session.project,
        user=session.user,
        question=question,
        top_k=ai_config.rag_top_k,
    )
    RetrievedChunk.objects.bulk_create(
        [
            RetrievedChunk(
                query=query,
                chunk=hit.chunk,
                rank=hit.rank,
                vector_score=hit.vector_score,
                lexical_score=hit.lexical_score,
                final_score=hit.final_score,
            )
            for hit in hits
        ]
    )
    answer = RagAnswer.objects.create(
        query=query,
        body=reply.body,
        summary=f"{reply.intent_label} / {reply.recommendation_label}",
        grounded_findings="\n".join(f"{c.title} p.{c.page}: {c.quote}" for c in reply.citations),
        unverified_points="\n".join(reply.missing_information),
        follow_up_questions=reply.follow_up_questions,
        provider=ai_config.provider,
        model=RULE_BASED_MODEL,
    )
    AnswerCitation.objects.bulk_create(
        [
            AnswerCitation(answer=answer, chunk=hit.chunk, quoted_text=hit.chunk.text[:QUOTE_LENGTH])
            for hit in hits[:MAX_CITATIONS]
        ]
    )

    return answer


def respond(
    session: ChatSession,
    question: str,
    index=None,
    *,
    project=None,
    include_business: bool = True,
) -> ChatReply:
    """1 往復の会話を実行し、履歴として保存する。

    `project` を渡すと、その案件の現在値（進捗・課題・リスク・期限超過）を
    応答の冒頭へ添える。未指定ならセッションに紐づく案件を使う。
    """

    target_project = project or session.project
    hits = (
        search(index, question, project=project, include_business=include_business)
        if index is not None
        else []
    )
    reply = build_reply(question, hits, project_context_service.build(target_project))

    ChatMessage.objects.create(session=session, role=ChatMessage.Role.USER, content=question)
    answer = _persist_answer(session, question, hits, reply) if hits else None
    ChatMessage.objects.create(
        session=session,
        role=ChatMessage.Role.ASSISTANT,
        content=reply.body,
        answer=answer,
    )
    session.save(update_fields=["updated_at"])

    return reply
