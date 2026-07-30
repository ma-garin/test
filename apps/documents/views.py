"""ドキュメント管理画面。"""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.core.pagination import page_window, paginate, query_without_page
from apps.documents import selectors
from apps.documents.services import extractors, registration, template_mapping
from apps.documents.services.validation import EXTENSION_TO_FILE_TYPE, MAX_FILE_SIZE_BYTES
from apps.projects.selectors import projects_for

#: ひな型はカード表示で 1 件が縦に長い。一覧の既定件数では画面に収まらない。
CARDS_PER_PAGE = 8


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

    page = paginate(selectors.documents_for(request.user, request.tenant), request)

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
            "page_title": "ドキュメント登録",
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

    return render(
        request,
        "pages/template_list.html",
        {
            "cards": page.object_list,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            # 集計は全件から取る。ページを送るたびに KPI が変わると数字を信用できない。
            "template_total": len(cards),
            "mapped_total": sum(card.mapped_count for card in cards),
            "page_title": "ひな型管理",
        },
    )


@login_required
def upload(request: HttpRequest) -> HttpResponse:
    """文書アップロード。検証結果を同じ画面へ返す。

    成功時もリダイレクトしないのは、重複警告など「登録はできたが確認してほしい
    こと」を利用者に必ず見せるため。
    """

    tenant = _current_tenant(request)
    projects = projects_for(request.user, request.tenant)
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
            "page_title": "文書アップロード",
        },
    )
