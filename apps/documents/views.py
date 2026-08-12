"""ドキュメント管理画面。"""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.core.pagination import page_window, paginate, query_without_page
from apps.documents import selectors
from apps.documents.models import DocumentStatus, Template
from apps.documents.services import extractors, registration, template_mapping
from apps.documents.services.validation import EXTENSION_TO_FILE_TYPE, MAX_FILE_SIZE_BYTES
from apps.projects.selectors import projects_for

#: ひな型はカード表示で 1 件が縦に長い。一覧の既定件数では画面に収まらない。
CARDS_PER_PAGE = 8

#: 案件に紐づかない文書（テナント共通ナレッジ）を指す絞り込み値。
SHARED_SCOPE = "shared"

#: `Document.needs_reindex` と同じ判定を ORM で書いたもの。
#: プロパティのままでは絞り込みに使えない。判定を二重に持つので必ず揃えること。
NEEDS_REINDEX = Q(status=DocumentStatus.ACTIVE) & (
    Q(last_indexed_at__isnull=True) | Q(last_indexed_at__lt=F("updated_at"))
)

#: 「登録した＝検索できる」という誤解が繰り返されるため、準備の段取りを画面に出す。
READINESS_STEPS = ("アップロード", "本文抽出", "インデックス", "検索")

#: マッピング状態ごとの次の設定作業。状態バッジだけでは次に何をすべきか分からない。
NEXT_SETUP_ACTIONS = {
    Template.MappingStatus.UNCONFIGURED: "項目マッピングを作成する（AI 提案 → 人が確認・承認）",
    Template.MappingStatus.DRAFT: "下書きのセル位置を確認して承認する",
    Template.MappingStatus.NEEDS_REVIEW: "指摘された項目を修正して再承認する",
    Template.MappingStatus.APPROVED: "追加の設定はありません。回答の出力先として使えます",
}


def _readiness(documents) -> list[dict[str, object]]:
    """準備 4 段の進み具合と現在地。色ではなく語で状態を示す。"""

    done = [
        documents.exists(),
        documents.filter(pages__isnull=False).exists(),
        documents.filter(last_indexed_at__isnull=False).exists(),
        # 検索は準備の到達点。ここが現在地になったら使える、という意味で常に未完了扱い。
        False,
    ]

    completed = 0

    for flag in done:
        if not flag:
            break

        completed += 1

    steps: list[dict[str, object]] = []

    for index, label in enumerate(READINESS_STEPS):
        if index < completed:
            state, tone = "完了", "g"
        elif index == completed:
            state, tone = "対応中（現在地）", "a"
        else:
            state, tone = "未着手", "n"

        steps.append({"number": index + 1, "label": label, "state": state, "tone": tone})

    return steps


def _apply_document_filters(request: HttpRequest, projects, documents):
    """GET の絞り込みを適用し、(絞り込み後, 選択値, 適用中の条件) を返す。

    不正な値は 500 にせず「未指定」へ落とす。外部入力は信用しない。
    """

    status = request.GET.get("status", "").strip()

    if status not in DocumentStatus.values:
        status = ""

    raw_project = request.GET.get("project", "").strip()
    project = None if raw_project == SHARED_SCOPE else _selected_project(projects, raw_project)
    reindex = request.GET.get("reindex", "").strip()

    if reindex not in {"yes", "no"}:
        reindex = ""

    applied: list[str] = []

    if status:
        documents = documents.filter(status=status)
        applied.append(f"文書状態: {DocumentStatus(status).label}")

    if raw_project == SHARED_SCOPE:
        documents = documents.filter(project__isnull=True)
        applied.append("案件: テナント共通ナレッジ")
    elif project is not None:
        documents = documents.filter(project=project)
        applied.append(f"案件: {project.name}")

    if reindex == "yes":
        documents = documents.filter(NEEDS_REINDEX)
        applied.append("再抽出要否: 必要")
    elif reindex == "no":
        documents = documents.exclude(NEEDS_REINDEX)
        applied.append("再抽出要否: 不要")

    selected = SHARED_SCOPE if raw_project == SHARED_SCOPE else (str(project.pk) if project else "")

    return documents, {"status": status, "project": selected, "reindex": reindex}, applied


def _current_tenant(request: HttpRequest):
    """参照中テナント。未選択ならユーザー所属テナントへ落とす。"""

    return request.tenant or request.user.tenant


def _selected_project(projects, raw_value: str | None):
    """フォームで選ばれた案件。参照可能な案件の中からしか解決しない。

    不正な ID が来ても 500 にせず「未選択」として扱う。外部入力を信用しない。
    """

    if not raw_value:
        return None

    try:
        return projects.filter(pk=raw_value).first()
    except (ValueError, ValidationError, TypeError):
        return None


@login_required
def document_list(request: HttpRequest) -> HttpResponse:
    """文書台帳。先頭 200 件で打ち切ると総件数と表示が食い違うため、ページで切る。"""

    projects = projects_for(request.user, request.tenant)
    all_documents = selectors.documents_for(request.user, request.tenant)
    documents, filters, applied = _apply_document_filters(request, projects, all_documents)
    page = paginate(documents, request)

    return render(
        request,
        "pages/document_list.html",
        {
            "documents": page.object_list,
            # 登録済みでも本文が取れていなければ検索に出ない。台帳で抽出状態まで見せる。
            "rows": selectors.extraction_rows(page.object_list),
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "projects": projects,
            "status_choices": DocumentStatus.choices,
            "filters": filters,
            "applied_filters": applied,
            "is_filtered": bool(applied),
            # 絞り込み前の母数。表示件数だけだと「消えた」のか「絞れている」のか分からない。
            "total_count": all_documents.count(),
            # 行が見えているときは邪魔になるので、空のときだけ準備ステップを出す。
            "readiness_steps": [] if page.object_list else _readiness(all_documents),
            "page_title": "ナレッジ一覧",
        },
    )


@login_required
@require_POST
def extract_document(request: HttpRequest, pk: UUID) -> HttpResponse:
    """本文抽出とインデックス構築を実行する。

    抽出の失敗は例外ではなくジョブとして記録されるため、ここでは常に台帳へ戻す。
    結果（抽出済み / 失敗理由）は台帳の行に出る。
    """

    document = selectors.documents_for(request.user, request.tenant).filter(pk=pk).first()

    if document is None:
        # 他テナントの文書 ID を推測されても存在有無を漏らさない。
        raise Http404("文書が見つかりません。")

    extractors.ingest(document)

    return redirect("documents:list")


@login_required
def template_list(request: HttpRequest) -> HttpResponse:
    """ひな型台帳。項目マッピングまで開いて見せる。

    「ひな型は RAG 対象に含めない」は旧実装からの分離方針。画面に明示しないと
    利用者が検索対象と誤解するため、注記を必ず出す。
    """

    templates = selectors.templates_for(request.user, request.tenant)
    cards = template_mapping.build_cards(templates)
    # 1 件がカード（項目マッピング表を含む）なので、既定の 50 件では縦に伸びすぎる。
    page = paginate(cards, request, per_page=CARDS_PER_PAGE)
    items = [
        {
            "card": card,
            "next_action": NEXT_SETUP_ACTIONS.get(
                card.template.mapping_status,
                "マッピング状態を確認する",
            ),
        }
        for card in page.object_list
    ]

    return render(
        request,
        "pages/template_list.html",
        {
            "cards": items,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            # 集計は全件から取る。ページを送るたびに KPI が変わると数字を信用できない。
            "template_total": len(cards),
            "mapped_total": sum(card.mapped_count for card in cards),
            "page_title": "ひな型一覧",
        },
    )


@login_required
def upload(request: HttpRequest) -> HttpResponse:
    """ナレッジ登録。検証結果を同じ画面へ返す。

    成功時もリダイレクトしないのは、重複警告など「登録はできたが確認してほしい
    こと」を利用者に必ず見せるため。
    """

    tenant = _current_tenant(request)
    projects = projects_for(request.user, request.tenant)
    documents = selectors.documents_for(request.user, request.tenant)
    result = None

    if request.method == "POST":
        result = registration.register(
            uploaded_file=request.FILES.get("file"),
            tenant=tenant,
            project=_selected_project(projects, request.POST.get("project")),
            title=request.POST.get("title", ""),
            source_note=request.POST.get("source_note", ""),
            user=request.user,
        )

    return render(
        request,
        "pages/document_upload.html",
        {
            "result": result,
            "projects": projects,
            "accepted_extensions": ", ".join(sorted(EXTENSION_TO_FILE_TYPE)),
            "max_size_mb": MAX_FILE_SIZE_BYTES // (1024 * 1024),
            # 1 件も無いうちは「登録の次に何が要るか」が分からない。準備の段取りを出す。
            "readiness_steps": [] if documents.exists() else _readiness(documents),
            "page_title": "ナレッジ登録",
        },
    )
