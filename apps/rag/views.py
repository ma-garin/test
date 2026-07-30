"""RAG 検索画面。

回答生成（LLM 呼び出し）はまだ配線していない。検索と根拠表示まではこの画面で完結し、
回答生成は `apps.agents` のオーケストレーター経由に寄せる方針。
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.rag.models import VectorIndex
from apps.rag.services.retriever import search


@login_required
def search_view(request: HttpRequest) -> HttpResponse:
    question = request.GET.get("q", "").strip()
    index = _current_index(request)
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
    return render(
        request,
        "pages/rag_chat.html",
        {"sessions": request.user.chat_sessions.all()[:50], "page_title": "チャットモード"},
    )


def _current_index(request: HttpRequest) -> VectorIndex | None:
    """現在のテナントの共通インデックス。

    案件を選択している場合に案件別インデックスへ切り替える処理は、案件選択 UI と
    合わせて実装する。
    """

    if request.tenant is None:
        return None

    return VectorIndex.objects.filter(tenant=request.tenant, project__isnull=True).first()
