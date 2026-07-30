"""RAG 検索・チャット画面。

チャットの応答生成は `apps.rag.services.chat`（ルールベース）に置く。ADR-0003 の
とおり LLM は呼ばず、検索結果と根拠評価だけで応答を組み立てる。
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.rag import selectors
from apps.rag.models import ChatSession
from apps.rag.services import chat
from apps.rag.services.retriever import search

#: セッションのタイトルに使う、最初の質問の長さ。
TITLE_LENGTH = 60


def _current_tenant(request: HttpRequest):
    return request.tenant or request.user.tenant


@login_required
def search_view(request: HttpRequest) -> HttpResponse:
    question = request.GET.get("q", "").strip()
    index = selectors.current_index(_current_tenant(request))
    hits = search(index, question) if (question and index) else []

    return render(
        request,
        "pages/rag_search.html",
        {
            "question": question,
            "index": index,
            "hits": hits,
            "page_title": "RAG検索",
        },
    )


@login_required
def chat_view(request: HttpRequest) -> HttpResponse:
    """会話履歴の表示と質問の送信を 1 画面で扱う。"""

    tenant = _current_tenant(request)

    if request.method == "POST":
        return _post_message(request, tenant)

    session = selectors.chat_session_for(request.user, tenant, request.GET.get("session"))

    return render(
        request,
        "pages/rag_chat.html",
        {
            "sessions": selectors.chat_sessions_for(request.user, tenant)[:50],
            "session": session,
            # `messages` は Django のメッセージフレームワークと名前が衝突するため避ける。
            "chat_messages": selectors.messages_of(session),
            "index": selectors.current_index(tenant),
            "page_title": "チャットモード",
        },
    )


def _post_message(request: HttpRequest, tenant) -> HttpResponse:
    """質問を受け取り、応答を保存してから GET へ戻す（PRG）。

    リロードで同じ質問が二重登録されるのを避けるため、必ずリダイレクトで返す。
    """

    question = request.POST.get("message", "").strip()
    session = selectors.chat_session_for(request.user, tenant, request.POST.get("session"))

    if tenant is None or not question:
        return redirect(_chat_url(session))

    if session is None:
        session = ChatSession.objects.create(
            tenant=tenant,
            user=request.user,
            title=question[:TITLE_LENGTH],
        )

    chat.respond(session, question, selectors.current_index(tenant, session.project))

    return redirect(_chat_url(session))


def _chat_url(session: ChatSession | None) -> str:
    url = reverse("rag:chat")

    return f"{url}?session={session.pk}" if session is not None else url
