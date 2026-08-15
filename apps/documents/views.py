"""ドキュメント管理画面。"""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.constants import Action
from apps.accounts.services import permissions
from apps.core.pagination import page_window, paginate, query_without_page
from apps.documents import selectors
from apps.documents.services import excel_export, extractors, registration, template_mapping
from apps.documents.services.validation import EXTENSION_TO_FILE_TYPE, MAX_FILE_SIZE_BYTES
from apps.projects.selectors import projects_for, scoped_projects_for

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

    # 抽出は本文とインデックスを作る書き込み。参照だけの利用者には実行させない。
    # 案件に紐づく文書ならその案件の役割で判定される。
    permissions.require(request.user, Action.EDIT, document)

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
def template_export(request: HttpRequest, pk: UUID) -> HttpResponse:
    """ひな型へ実データを書き出し、結果を画面で見せてからダウンロードさせる。

    いきなりファイルを返さないのは、「何が出力できなかったか」を伝える場所が
    無くなるため。空欄が『値なし』なのか『出力漏れ』なのかを受け取った人が
    判断できない成果物は、実務では出し直しになる。

    出力に失敗しても 500 にしない。openpyxl 未導入・ひな型ファイル欠損は運用中に
    普通に起こるので、理由を画面に出して次の手が打てる状態にする。
    """

    template = selectors.templates_for(request.user, request.tenant).filter(pk=pk).first()

    if template is None:
        # 他テナントのひな型 ID を推測されても存在有無を漏らさない。
        raise Http404("ひな型が見つかりません。")

    deliverable = _selected_deliverable(request)
    projects = scoped_projects_for(request)
    project = _selected_project(projects, request.GET.get("project"))

    if project is None and deliverable is not None:
        # 成果物から来た場合は、その成果物の案件を既定にする（選び直しの手間を省く）。
        project = deliverable.project

    result = excel_export.export(template, project=project, deliverable=deliverable)

    if request.GET.get("download") and result.ok:
        return _excel_response(result)

    return render(
        request,
        "pages/template_export.html",
        {
            "template": template,
            "projects": projects,
            "project": project,
            "deliverable": deliverable,
            "result": result,
            "download_query": _download_query(project, deliverable),
            "page_title": f"Excel出力 / {template.name}",
        },
    )


def _selected_deliverable(request: HttpRequest):
    """出力対象の成果物。参照できる案件のものしか解決しない。"""

    raw_value = request.GET.get("deliverable")

    if not raw_value:
        return None

    # アプリ間の循環 import を避けるため関数内で読み込む。
    from apps.pmo import selectors as pmo_selectors

    try:
        return pmo_selectors.deliverables_for(request.user, request.tenant).filter(
            pk=raw_value
        ).first()
    except (ValueError, ValidationError, TypeError):
        return None


def _download_query(project, deliverable) -> str:
    """ダウンロードリンクの検索文字列。画面と同じ条件で作り直させる。"""

    params = {"download": "1"}

    if project is not None:
        params["project"] = str(project.pk)

    if deliverable is not None:
        params["deliverable"] = str(deliverable.pk)

    return urlencode(params)


def _excel_response(result: excel_export.ExportResult) -> HttpResponse:
    """生成した Excel を返す。日本語ファイル名は RFC 5987 形式で渡す。"""

    response = HttpResponse(result.content, content_type=result.content_type)
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", result.filename).strip("_") or "export.xlsx"
    response["Content-Disposition"] = (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(result.filename)}"
    )

    return response


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
        project = _selected_project(projects, request.POST.get("project"))
        # 登録は書き込み。案件が選ばれていればその案件の役割で、選ばれて
        # いなければテナント単位で判定する。
        permissions.require(request.user, Action.EDIT, project)

        result = registration.register(
            uploaded_file=request.FILES.get("file"),
            tenant=tenant,
            project=project,
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
