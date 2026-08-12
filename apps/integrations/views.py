"""外部連携の設定・同期実行・履歴。

接続の取得は必ず `connections_for` で絞る。テナント越境は「見えない」ではなく
「存在しない（404）」として扱い、ID の総当たりで他テナントの存在有無が漏れないようにする。

接続の追加・編集はテナント管理者に限る。接続先を差し替えられると、取込元を
すり替えて内部データを汚染できてしまうため。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.pagination import page_window, paginate, query_without_page
from apps.integrations import selectors
from apps.integrations.forms import ConnectionForm
from apps.integrations.models import Connection, SyncJob
from apps.integrations.services.connections import check_connection, save_connection
from apps.integrations.services.pipeline import build_pipeline_overview
from apps.integrations.services.sync import run_pull
from apps.projects.selectors import projects_for

LIST_URL = "integrations:list"


@dataclass(frozen=True)
class ConnectionRow:
    """一覧の 1 行。接続と直近ジョブを組にして、テンプレートから追加クエリを出さない。"""

    connection: Connection
    latest_job: SyncJob | None


def _require_tenant_admin(user) -> None:
    """接続の変更権限。`is_tenant_admin` はスーパーユーザーを含む。"""

    if not user.is_tenant_admin:
        raise PermissionDenied("接続の追加・編集はテナント管理者のみ行えます")


@login_required
def connection_list(request: HttpRequest) -> HttpResponse:
    """接続一覧。モックか実 API かを行ごとに明示する。"""

    connections = selectors.connections_for(request.user, request.tenant)
    page = paginate(connections, request)
    latest = selectors.latest_jobs_by_connection(page.object_list)

    rows = [
        ConnectionRow(connection=connection, latest_job=latest.get(connection.pk))
        for connection in page.object_list
    ]

    return render(
        request,
        "pages/integration_list.html",
        {
            "rows": rows,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "can_edit": request.user.is_tenant_admin,
            "page_title": "外部連携",
        },
    )


@login_required
def connection_create(request: HttpRequest) -> HttpResponse:
    """接続の追加。"""

    _require_tenant_admin(request.user)

    if request.tenant is None:
        # テナントが決まらないと、どこへ属する接続かを決められない。
        messages.error(request, "テナントを選択してから接続を追加してください。")

        return redirect(LIST_URL)

    projects = projects_for(request.user, request.tenant)
    form = ConnectionForm(request.POST or None, projects=projects)

    if request.method == "POST" and form.is_valid():
        connection = save_connection(form, tenant=request.tenant)
        messages.success(request, f"接続「{connection.name}」を追加しました。")

        return redirect(LIST_URL)

    return render(
        request,
        "pages/integration_form.html",
        {
            "form": form,
            "form_title": "接続の追加",
            "form_subtitle": "資格情報は環境変数名だけを登録します。値そのものは保存しません。",
            "page_title": "接続の追加",
        },
    )


@login_required
def connection_edit(request: HttpRequest, pk) -> HttpResponse:
    """接続の編集。"""

    _require_tenant_admin(request.user)

    connection = get_object_or_404(
        selectors.connections_for(request.user, request.tenant), pk=pk
    )
    projects = projects_for(request.user, connection.tenant)
    form = ConnectionForm(request.POST or None, instance=connection, projects=projects)

    if request.method == "POST" and form.is_valid():
        save_connection(form, tenant=connection.tenant)
        messages.success(request, f"接続「{connection.name}」を更新しました。")

        return redirect(LIST_URL)

    return render(
        request,
        "pages/integration_form.html",
        {
            "form": form,
            "connection": connection,
            "form_title": f"接続の編集: {connection.name}",
            "form_subtitle": "資格情報は環境変数名だけを登録します。値そのものは保存しません。",
            "page_title": "接続の編集",
        },
    )


@login_required
@require_POST
def connection_check(request: HttpRequest, pk) -> HttpResponse:
    """疎通確認。設定を信じ込む前に、利用者が自分で試せるようにする。"""

    connection = get_object_or_404(
        selectors.connections_for(request.user, request.tenant), pk=pk
    )
    status = check_connection(connection)

    if status.ok:
        messages.success(request, f"{connection.name}: {status.message}")
    else:
        messages.error(request, f"{connection.name}: {status.message}")

    return redirect(LIST_URL)


@login_required
@require_POST
def connection_sync(request: HttpRequest, pk) -> HttpResponse:
    """同期実行。結果は件数まで含めて伝える（「実行した」だけでは検証できない）。"""

    connection = get_object_or_404(
        selectors.connections_for(request.user, request.tenant), pk=pk
    )
    job = run_pull(connection, user=request.user)
    text = f"{connection.name}: {job.message}"

    if job.status == SyncJob.Status.SUCCEEDED:
        messages.success(request, text)
    elif job.status == SyncJob.Status.PARTIAL:
        messages.warning(request, text)
    else:
        messages.error(request, text)

    return redirect(LIST_URL)


@login_required
def job_list(request: HttpRequest) -> HttpResponse:
    """同期履歴。内訳（新規・更新・変更なし・失敗）と所要時間を残す。"""

    jobs = selectors.sync_jobs_for(request.user, request.tenant)
    page = paginate(jobs, request)

    return render(
        request,
        "pages/integration_job_list.html",
        {
            "jobs": page.object_list,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "page_title": "同期履歴",
        },
    )


@login_required
def pipeline(request: HttpRequest) -> HttpResponse:
    """パイプライン監視。同期が止まっていることに気づける場所を 1 つに集める。

    接続ごとの最終同期・最終成功からの経過・直近ジョブの成否・RAG インデックスの
    最終構築を 1 画面にまとめる。しきい値超過は警告として最上部に出す。
    """

    overview = build_pipeline_overview(request.user, request.tenant)

    return render(
        request,
        "pages/integration_pipeline.html",
        {"overview": overview, "page_title": "パイプライン監視"},
    )
