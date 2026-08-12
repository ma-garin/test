"""RAG 検索・チャット画面。

チャットの応答生成は `apps.rag.services.chat`（ルールベース）に置く。ADR-0003 の
とおり LLM は呼ばず、検索結果と根拠評価だけで応答を組み立てる。
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.documents.models import Document
from apps.rag import scopes, selectors
from apps.rag.models import ChatSession, EvaluationSuite, GoldenQuestion
from apps.rag.services import chat
from apps.rag.services import project_context as project_context_service
from apps.rag.services.evaluation import golden as golden_service
from apps.rag.services.evaluation import metric_deltas, run_evaluation
from apps.rag.services.retriever import search

#: セッションのタイトルに使う、最初の質問の長さ。
TITLE_LENGTH = 60

#: 評価画面の既定スイート。
DEFAULT_SUITE = EvaluationSuite.RETRIEVAL

#: 「根拠が十分」と表示するために必要な引用件数。これ未満は限定的として扱う。
MIN_SUFFICIENT_HITS = 3


def _evidence_summary(question: str, scope, hits: list) -> dict:
    """検索結果の十分性を、要点の近くに出すための表示用まとめ。

    判定は引用件数だけで行う。検索・生成ロジックには手を入れず、表示の責務に閉じる。
    引用が 0 件のときは「根拠なし」と言い切る。無いものを弱い肯定で濁さないため。
    """

    count = len(hits)

    if not question:
        level, label = "none", "未検索"
    elif count == 0:
        level, label = "none", "根拠なし"
    elif count < MIN_SUFFICIENT_HITS:
        level, label = "weak", "根拠が限定的"
    else:
        level, label = "ok", "複数の根拠あり"

    return {
        "count": count,
        "level": level,
        "label": label,
        "scope_label": scope.label,
        "is_grounded": count > 0,
    }


def _current_tenant(request: HttpRequest):
    return request.tenant or request.user.tenant


@login_required
def search_view(request: HttpRequest) -> HttpResponse:
    """RAG 検索。検索範囲（テナント / 案件 / 業務データ）を切り替えられる。"""

    tenant = _current_tenant(request)
    scope = scopes.resolve(request, request.GET.get("scope"), tenant)
    question = request.GET.get("q", "").strip()
    # 未入力のまま検索されたことを、まだ押していない状態と区別する。
    # 同じ画面を返すだけだと「押しても何も起きない」不具合に見える。
    submitted_empty = "q" in request.GET and not question
    hits = (
        search(
            list(scope.indexes),
            question,
            project=scope.project,
            include_business=scope.include_business,
        )
        if (question and scope.is_usable)
        else []
    )

    return render(
        request,
        "pages/rag_search.html",
        {
            "question": question,
            "submitted_empty": submitted_empty,
            "index": scope.primary_index,
            "scope": scope,
            "scope_choices": scopes.SCOPE_CHOICES,
            "hits": hits,
            "evidence": _evidence_summary(question, scope, list(hits)),
            # 取得時点を出さないと、いつの索引に基づく結果かを読み手が確かめられない。
            "searched_at": timezone.now(),
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
    scope = scopes.resolve(request, request.GET.get("scope"), tenant)

    return render(
        request,
        "pages/rag_chat.html",
        {
            "sessions": selectors.chat_sessions_for(request.user, tenant)[:50],
            "session": session,
            # `messages` は Django のメッセージフレームワークと名前が衝突するため避ける。
            "chat_messages": selectors.messages_of(session),
            "index": scope.primary_index,
            "scope": scope,
            "scope_choices": scopes.SCOPE_CHOICES,
            "project_context": project_context_service.build(
                scope.project or getattr(session, "project", None)
            ),
            "page_title": "チャットモード",
        },
    )


def _post_message(request: HttpRequest, tenant) -> HttpResponse:
    """質問を受け取り、応答を保存してから GET へ戻す（PRG）。

    リロードで同じ質問が二重登録されるのを避けるため、必ずリダイレクトで返す。
    """

    question = request.POST.get("message", "").strip()
    session = selectors.chat_session_for(request.user, tenant, request.POST.get("session"))
    scope = scopes.resolve(request, request.POST.get("scope"), tenant)

    if tenant is None or not question:
        return redirect(_chat_url(session))

    if session is None:
        session = ChatSession.objects.create(
            tenant=tenant,
            # 選択中の案件を残し、以降の応答にも案件文脈を効かせる。
            project=scope.project,
            user=request.user,
            title=question[:TITLE_LENGTH],
        )

    chat.respond(
        session,
        question,
        list(scope.indexes),
        project=scope.project or session.project,
        include_business=scope.include_business,
    )

    return redirect(_chat_url(session))


def _chat_url(session: ChatSession | None) -> str:
    url = reverse("rag:chat")

    return f"{url}?session={session.pk}" if session is not None else url


@login_required
def evaluation_view(request: HttpRequest) -> HttpResponse:
    """RAG 評価。実行・指標・Golden Dataset・履歴を 1 画面で扱う（#68〜#71）。"""

    tenant = _current_tenant(request)

    if request.method == "POST":
        return _post_evaluation(request, tenant)

    suite = _resolved_suite(request.GET.get("suite"))
    run = selectors.latest_evaluation_run(tenant, suite)
    deltas = metric_deltas(run)
    cases = list(run.cases.all()) if run is not None else []
    failed_cases = [case for case in cases if case.evaluable and not case.passed]
    only_failed = request.GET.get("cases") == "failed"

    return render(
        request,
        "pages/rag_evaluation.html",
        {
            "suites": EvaluationSuite.choices,
            "suite": suite,
            "run": run,
            "deltas": deltas,
            # 悪化した指標だけを先頭へ集約する。良い数字に埋もれさせない。
            "regressions": [delta for delta in deltas if delta.tone == "r"],
            "cases": failed_cases if only_failed else cases,
            "case_total": len(cases),
            "failed_count": len(failed_cases),
            "only_failed": only_failed,
            "history": list(selectors.evaluation_runs_for(tenant)[:10]),
            "golden_rows": golden_service.golden_overview(tenant),
            "documents": selectors.document_choices_for(tenant),
            "index": selectors.current_index(tenant),
            "error": request.GET.get("error", ""),
            "page_title": "RAG評価",
        },
    )


def _resolved_suite(value: str | None) -> str:
    """未知の値を既定へ落とす。URL 経由の入力を信用しない。"""

    return value if value in EvaluationSuite.values else DEFAULT_SUITE


def _post_evaluation(request: HttpRequest, tenant) -> HttpResponse:
    """評価実行と Golden 登録。どちらも PRG で GET へ戻す。"""

    if tenant is None:
        return redirect(reverse("rag:evaluation"))

    if request.POST.get("action") == "add_golden":
        return _create_golden(request, tenant)

    suite = _resolved_suite(request.POST.get("suite"))
    run_evaluation(tenant=tenant, suite=suite, user=request.user)

    return redirect(f"{reverse('rag:evaluation')}?suite={suite}")


def _split_terms(raw: str) -> list[str]:
    """読点・カンマ・改行のいずれでも区切れるようにする。"""

    normalized = raw.replace("、", ",").replace("\n", ",")

    return [term.strip() for term in normalized.split(",") if term.strip()]


def _valid_uuids(values: list[str]) -> list[str]:
    """UUID として読めるものだけを残す。不正値でクエリを壊さないため。"""

    import uuid

    kept: list[str] = []

    for value in values:
        try:
            kept.append(str(uuid.UUID(value)))
        except (ValueError, AttributeError, TypeError):
            continue

    return kept


def _create_golden(request: HttpRequest, tenant) -> HttpResponse:
    """Golden を 1 件登録する。画面から増やせないと評価は続かない。"""

    question = request.POST.get("question", "").strip()

    if not question:
        return redirect(f"{reverse('rag:evaluation')}?error=question")

    golden = GoldenQuestion.objects.create(
        tenant=tenant,
        question=question,
        category=request.POST.get("category", "").strip()[:60],
        expected_terms=_split_terms(request.POST.get("expected_terms", "")),
        must_abstain=bool(request.POST.get("must_abstain")),
    )
    golden.expected_documents.set(
        Document.objects.filter(
            tenant=tenant,
            pk__in=_valid_uuids(request.POST.getlist("expected_documents")),
            deleted_at__isnull=True,
        )
    )
    golden.sync_expected_snapshot()

    return redirect(reverse("rag:evaluation"))
