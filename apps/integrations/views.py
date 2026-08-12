"""外部連携の設定・同期実行・履歴。

接続の取得は必ず `connections_for` で絞る。テナント越境は「見えない」ではなく
「存在しない（404）」として扱い、ID の総当たりで他テナントの存在有無が漏れないようにする。

接続の追加・編集はテナント管理者に限る。接続先を差し替えられると、取込元を
すり替えて内部データを汚染できてしまうため。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.forms import BoundField
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.pagination import page_window, paginate, query_without_page
from apps.integrations import selectors
from apps.integrations.forms import ConnectionForm
from apps.integrations.models import Connection, SyncJob
from apps.integrations.services.connections import check_connection, save_connection
from apps.integrations.services.pipeline import ConnectionHealth, build_pipeline_overview
from apps.integrations.services.sync import run_pull
from apps.projects.selectors import projects_for

LIST_URL = "integrations:list"


#: 「止まっている」と見なすジョブ状態。partial は一部が入っていないので異常に含める。
ALERT_STATUSES = (SyncJob.Status.FAILED, SyncJob.Status.PARTIAL)

#: 同期履歴の成否フィルタ。partial（一部失敗）を成功側に混ぜない。混ぜると
#: 「動いているのに取り込めていない」状態が成功の中に埋もれる。
JOB_RESULT_FILTERS: dict[str, tuple[str, ...]] = {
    "ok": (SyncJob.Status.SUCCEEDED,),
    "ng": (SyncJob.Status.FAILED, SyncJob.Status.PARTIAL),
    "running": (SyncJob.Status.QUEUED, SyncJob.Status.RUNNING),
}

#: 成否フィルタの表示順とラベル。
JOB_RESULT_CHOICES: tuple[tuple[str, str], ...] = (
    ("ok", "成功のみ"),
    ("ng", "失敗・一部失敗"),
    ("running", "待機・実行中"),
)

#: 期間フィルタ（日数）。任意の値は受け付けず、この3つだけを許す。
JOB_PERIOD_CHOICES: tuple[tuple[str, str], ...] = (
    ("7", "直近 7 日"),
    ("30", "直近 30 日"),
    ("90", "直近 90 日"),
)

#: 接続設定の入力順。初回設定で迷わないよう、画面の見出しもこの順に並べる。
#: 4 つ目（疎通確認）は入力欄を持たないのでテンプレート側に置く。
CONNECTION_STEPS: tuple[tuple[int, str, str, tuple[str, ...]], ...] = (
    (
        1,
        "接続先",
        "どのツールの、どこから取り込むかを決めます。",
        ("provider", "name", "base_url"),
    ),
    (
        2,
        "認証の参照名",
        "保存するのは環境変数の「名前」だけです。トークンの値そのものは入力も表示もしません。",
        ("credential_env", "mode"),
    ),
    (
        3,
        "取込範囲",
        "取り込む先の案件と、連携先ごとの絞り込みを指定します。",
        ("project", "config", "is_active"),
    ),
)

#: 疎通確認のステップ番号。入力欄が無いので定数で持つ。
CHECK_STEP = 4


@dataclass(frozen=True)
class ConnectionRow:
    """一覧の 1 行。接続と直近ジョブを組にして、テンプレートから追加クエリを出さない。

    運用者が一覧だけで判断できるよう、「何を取り込むか」「外部へ書き戻すか」
    「最後に成功したのはいつか」「失敗した理由は何か」を行の主情報として持たせる。
    """

    connection: Connection
    latest_job: SyncJob | None
    last_success_job: SyncJob | None = None

    @property
    def last_success_at(self):
        """最後に成功した同期の時刻。終了時刻が無ければ作成時刻で代用する。"""

        job = self.last_success_job

        if job is None:
            return None

        return job.finished_at or job.created_at

    @property
    def sync_target(self) -> str:
        """この接続が取り込むもの。行を見て「何が入るのか」が分かるようにする。"""

        connection = self.connection

        if connection.can_pull_issues:
            return "課題・チケット（外部 → 内部）"

        if connection.can_pull_documents:
            return "文書（外部 → 内部）"

        if connection.can_pull_activity:
            return "コミット統計（外部 → 内部）"

        if connection.can_notify:
            return "取込なし（通知の送信のみ）"

        return "未設定"

    @property
    def writes_back(self) -> bool:
        """外部へ何かを書き出すか。通知だけが唯一の書き出し経路。"""

        return self.connection.can_notify

    @property
    def write_back_label(self) -> str:
        if self.writes_back:
            return "通知の送信のみ（外部の課題は書き換えません）"

        return "外部へ書き込みません（読み取り専用）"

    @property
    def failure_reason(self) -> str:
        """直近の同期が失敗していれば、その理由。成功していれば空文字。"""

        job = self.latest_job

        if job is None or job.status not in ALERT_STATUSES:
            return ""

        return job.message or "理由が記録されていません"


@dataclass(frozen=True)
class PipelineRow:
    """監視表の 1 行。異常を先頭へ固定するための順位と、失敗理由を添える。

    集計そのもの（`services/pipeline.py`）は表示都合を知らないので、
    並び順と文言はここで組み立てる。
    """

    health: ConnectionHealth

    @property
    def connection(self) -> Connection:
        return self.health.connection

    @property
    def latest_job(self) -> SyncJob | None:
        return self.health.latest_job

    @property
    def last_synced_at(self):
        return self.health.last_synced_at

    @property
    def last_success_at(self):
        return self.health.last_success_at

    @property
    def hours_since_success(self) -> float | None:
        return self.health.hours_since_success

    @property
    def is_stale(self) -> bool:
        return self.health.is_stale

    @property
    def staleness_label(self) -> str:
        return self.health.staleness_label

    @property
    def tone(self) -> str:
        return self.health.tone

    @property
    def job_failed(self) -> bool:
        return self.latest_job is not None and self.latest_job.status in ALERT_STATUSES

    @property
    def needs_attention(self) -> bool:
        return bool(self.connection.is_active and (self.is_stale or self.job_failed))

    @property
    def rank(self) -> int:
        """並び順。0 が最上位（最も先に見るべき行）。"""

        if not self.connection.is_active:
            return 3

        if self.is_stale:
            return 0

        if self.job_failed:
            return 1

        return 2

    @property
    def attention_reason(self) -> str:
        if self.is_stale and self.hours_since_success is None:
            return "一度も成功していない"

        if self.is_stale:
            return "同期が止まっている疑い"

        if self.job_failed:
            return f"直近の同期が{self.latest_job.get_status_display()}"

        return "正常"

    @property
    def failure_reason(self) -> str:
        """失敗理由。ジョブのメッセージが無い場合も無言にしない。"""

        if self.job_failed:
            return self.latest_job.message or "理由が記録されていません"

        if self.is_stale and self.hours_since_success is None:
            return "一度も同期に成功していません"

        if self.is_stale:
            return f"最終成功から {self.hours_since_success} 時間が経過しています"

        return ""


@dataclass(frozen=True)
class FormStep:
    """接続フォームの 1 ステップ。"""

    number: int
    title: str
    hint: str
    fields: tuple[BoundField, ...]


def _form_steps(form: ConnectionForm) -> tuple[FormStep, ...]:
    return tuple(
        FormStep(
            number=number,
            title=title,
            hint=hint,
            fields=tuple(form[name] for name in names),
        )
        for number, title, hint, names in CONNECTION_STEPS
    )


def _current_step(form: ConnectionForm, *, is_new: bool) -> int:
    """いま手を入れるべきステップ。初回設定で順序を見失わないための現在地。

    入力エラーがあれば、そのステップへ戻す。無ければ新規は 1 番目、
    既存の接続は残る作業が疎通確認だけなので 4 番目を現在地にする。
    """

    for number, _title, _hint, names in CONNECTION_STEPS:
        if any(form[name].errors for name in names):
            return number

    return 1 if is_new else CHECK_STEP


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
    succeeded = selectors.latest_successful_jobs_by_connection(page.object_list)

    rows = [
        ConnectionRow(
            connection=connection,
            latest_job=latest.get(connection.pk),
            last_success_job=succeeded.get(connection.pk),
        )
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
            "form_steps": _form_steps(form),
            "current_step": _current_step(form, is_new=True),
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
            "form_steps": _form_steps(form),
            "current_step": _current_step(form, is_new=False),
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


@dataclass(frozen=True)
class JobFilters:
    """同期履歴の絞り込み条件。GET から作り、そのまま画面へ返す。"""

    connection: str = ""
    connection_label: str = ""
    result: str = ""
    period: str = ""

    @property
    def is_active(self) -> bool:
        return bool(self.connection or self.result or self.period)

    @property
    def labels(self) -> tuple[str, ...]:
        """画面に出す「いま効いている条件」。何で絞ったか読めないと件数が信用できない。"""

        applied: list[str] = []

        if self.connection:
            applied.append(f"接続: {self.connection_label}")

        if self.result:
            applied.append(f"成否: {dict(JOB_RESULT_CHOICES)[self.result]}")

        if self.period:
            applied.append(f"期間: {dict(JOB_PERIOD_CHOICES)[self.period]}")

        return tuple(applied)


def _job_filters(request: HttpRequest, connections: list[Connection]) -> JobFilters:
    """GET を検証して条件へ変換する。

    接続 ID は「参照できる接続」に含まれるものだけを採用する。存在しない ID を
    そのままクエリへ渡すと、他テナントの ID かどうかを反応の差で探れてしまう。
    """

    raw_connection = request.GET.get("connection", "").strip()
    match = next((c for c in connections if str(c.pk) == raw_connection), None)
    result = request.GET.get("result", "").strip()
    period = request.GET.get("period", "").strip()

    return JobFilters(
        connection=str(match.pk) if match else "",
        connection_label=match.name if match else "",
        result=result if result in JOB_RESULT_FILTERS else "",
        period=period if period in dict(JOB_PERIOD_CHOICES) else "",
    )


def _filtered_jobs(jobs, filters: JobFilters):
    """条件を積む。空の条件は積まないので、既定は全件のまま。"""

    if filters.connection:
        jobs = jobs.filter(connection_id=filters.connection)

    if filters.result:
        jobs = jobs.filter(status__in=JOB_RESULT_FILTERS[filters.result])

    if filters.period:
        since = timezone.now() - timedelta(days=int(filters.period))
        jobs = jobs.filter(created_at__gte=since)

    return jobs


@login_required
def job_list(request: HttpRequest) -> HttpResponse:
    """同期履歴。内訳（新規・更新・変更なし・失敗）と所要時間を残す。

    どの接続が・いつ・どう失敗したかへ辿れないと履歴は読めないので、
    接続・成否・期間の 3 軸で絞り込む。条件は GET だけで表せるようにして、
    「条件をクリア」で必ず全件へ戻れる状態を保つ。
    """

    connections = list(
        selectors.connections_for(request.user, request.tenant).order_by("name")
    )
    filters = _job_filters(request, connections)
    jobs = _filtered_jobs(selectors.sync_jobs_for(request.user, request.tenant), filters)
    page = paginate(jobs, request)

    return render(
        request,
        "pages/integration_job_list.html",
        {
            "jobs": page.object_list,
            "connections": connections,
            "filters": filters,
            "applied_labels": filters.labels,
            "result_choices": JOB_RESULT_CHOICES,
            "period_choices": JOB_PERIOD_CHOICES,
            "match_count": page.paginator.count,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "page_title": "同期履歴",
        },
    )


@login_required
def pipeline(request: HttpRequest) -> HttpResponse:
    """同期の稼働状況。同期が止まっていることに気づける場所を 1 つに集める。

    接続ごとの最終同期・最終成功からの経過・直近ジョブの成否・RAG インデックスの
    最終構築を 1 画面にまとめる。しきい値超過は警告として最上部に出す。
    """

    overview = build_pipeline_overview(request.user, request.tenant)
    # 異常のある接続を先頭に固定する。既定の並び（連携先→名前）のままだと、
    # 止まっている接続が表の途中に埋もれて気づけない。
    rows = sorted(
        (PipelineRow(health=health) for health in overview.rows),
        key=lambda row: (row.rank, row.connection.name),
    )

    return render(
        request,
        "pages/integration_pipeline.html",
        {
            "overview": overview,
            "rows": rows,
            "attention_rows": [row for row in rows if row.needs_attention],
            "can_edit": request.user.is_tenant_admin,
            "page_title": "同期の稼働状況",
        },
    )
