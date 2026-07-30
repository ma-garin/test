"""チャット・インデックスの参照クエリ。

会話は個人の作業記録なので、テナント分離に加えて「本人のセッションだけ」を
ここで担保する。ビュー側で条件を書き足さないこと。
"""

from __future__ import annotations

from django.db.models import QuerySet

from apps.documents.models import Document, DocumentStatus
from apps.rag.models import ChatMessage, ChatSession, EvaluationRun, VectorIndex


def chat_sessions_for(user, tenant) -> QuerySet[ChatSession]:
    """本人が参照できるチャットセッション。"""

    queryset = ChatSession.objects.select_related("project")

    if user is None or not user.is_authenticated or tenant is None:
        return queryset.none()

    return queryset.filter(user=user, tenant=tenant, is_archived=False)


def chat_session_for(user, tenant, session_id) -> ChatSession | None:
    """指定セッション。未指定なら直近のセッションを返す。"""

    sessions = chat_sessions_for(user, tenant)

    if session_id:
        return sessions.filter(pk=session_id).first()

    return sessions.first()


def messages_of(session: ChatSession | None) -> list[ChatMessage]:
    """会話履歴。根拠の表示に使う引用まで一度に取っておく。"""

    if session is None:
        return []

    return list(
        session.messages.select_related("answer").prefetch_related(
            "answer__citations__chunk__document"
        )
    )


def current_index(tenant, project=None) -> VectorIndex | None:
    """検索に使うインデックス。案件別があればそれを優先する。"""

    if tenant is None:
        return None

    if project is not None:
        index = VectorIndex.objects.filter(tenant=tenant, project=project).first()

        if index is not None:
            return index

    return VectorIndex.objects.filter(tenant=tenant, project__isnull=True).first()


def evaluation_runs_for(tenant, project=None) -> QuerySet[EvaluationRun]:
    """評価履歴。テナント分離はここで担保する。"""

    if tenant is None:
        return EvaluationRun.objects.none()

    queryset = EvaluationRun.objects.filter(tenant=tenant).select_related("project", "executed_by")

    return queryset.filter(project=project) if project is not None else queryset


def latest_evaluation_run(tenant, suite: str, project=None) -> EvaluationRun | None:
    """指定スイートの最新実行。無ければ None（＝まだ測っていない）。"""

    return evaluation_runs_for(tenant, project).filter(suite=suite).first()


def document_choices_for(tenant) -> QuerySet[Document]:
    """Golden の期待文書として選べる文書（RAG 対象のみ）。"""

    if tenant is None:
        return Document.objects.none()

    return Document.objects.filter(
        tenant=tenant,
        status=DocumentStatus.ACTIVE,
        deleted_at__isnull=True,
    ).order_by("title")
