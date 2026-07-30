"""チャット・インデックスの参照クエリ。

会話は個人の作業記録なので、テナント分離に加えて「本人のセッションだけ」を
ここで担保する。ビュー側で条件を書き足さないこと。
"""

from __future__ import annotations

from django.db.models import QuerySet

from apps.rag.models import ChatMessage, ChatSession, VectorIndex


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
