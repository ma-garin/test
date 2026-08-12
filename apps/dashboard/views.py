"""管制ダッシュボード配下の画面。

参照は `apps.dashboard.selectors`、集計は `apps.dashboard.services` に置き、
ビューは「絞り込み条件を受け取って、組み立て済みの表示データを渡す」だけにする。
テナント分離は `projects_for()` を必ず入口に通すことで担保している。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.selectors import feedbacks_for
from apps.core.pagination import page_window, paginate, query_without_page
from apps.dashboard import selectors
from apps.dashboard.forms import InterventionDecisionForm
from apps.dashboard.models import Alert, InterventionProposal, KpiMeasurement
from apps.dashboard.services.decisions import (
    InterventionRow,
    build_change_report,
    build_intervention_report,
    build_risk_report,
)
from apps.dashboard.services.detection import kind_label, run_detection
from apps.dashboard.services.detection.findings import Finding, Skip
from apps.dashboard.services.gantt import build_gantt_chart
from apps.dashboard.services.input_rules import build_input_rule_report
from apps.dashboard.services.interventions import (
    AlreadyDecidedError,
    decide_intervention,
    is_pending,
)
from apps.dashboard.services.kpi import build_derived_rows, build_kpi_report
from apps.dashboard.services.milestones import build_milestone_report
from apps.dashboard.services.overview import Overview, build_overview
from apps.dashboard.services.poc_evaluation import (
    BUSINESS_DAY_NOTE,
    VERDICT_FAIL,
    build_poc_evaluation,
)
from apps.dashboard.services.progress import build_progress_report
from apps.dashboard.services.quality import build_quality_report
from apps.dashboard.services.tasks import TaskFilters, build_task_board
from apps.documents.selectors import documents_for
from apps.documents.services.requirement_coverage import build_coverage_report
from apps.projects.models import ChangeRequest, Risk, WbsTask
from apps.projects.permissions import approval_denied_reason, can_approve_in_project
from apps.projects.selectors import scoped_projects_for


def _page_context(page, request: HttpRequest) -> dict:
    """ページャ用のコンテキスト。絞り込み条件を保ったままページを送れるようにする。"""

    return {
        "page": page,
        "page_window": page_window(page),
        "page_query": query_without_page(request),
    }


def _projects(request: HttpRequest):
    """この画面が対象とする案件。全画面の入口。

    案件が選択されていればその1件、未選択なら参照できる全件。
    ここを通すことで、案件切替が管制配下の全画面へ一度に効く。
    """

    return scoped_projects_for(request)


#: UXP-01 / UXP-04: 分類ごとの着地先（URL 名・絞り込み・リンク文言）。
#: アラートも検知結果も「読めるだけの行」にしないため、必ずどこかの台帳へ着地させる。
LEDGER_DESTINATIONS: dict[str, tuple[str, str, str]] = {
    Alert.Category.SCHEDULE: ("dashboard:tasks", "due=overdue", "期限超過タスクを見る"),
    Alert.Category.QUALITY: ("projects:defect_list", "status=new", "不具合一覧を見る"),
    Alert.Category.RISK: ("dashboard:risk", "", "リスク一覧を見る"),
    Alert.Category.CHANGE: ("dashboard:change", "status=pending_approval", "変更要求を見る"),
    Alert.Category.RESOURCE: ("dashboard:tasks", "status=blocked", "ブロック中タスクを見る"),
}

#: 分類が未知のときの着地先。行き先なしにはしない。
DEFAULT_DESTINATION: tuple[str, str, str] = ("projects:issue_list", "status=open", "課題一覧を見る")

#: UXP-04: 判定不能のときに足りていないデータ。警告色だけでは何を用意すればよいか分からない。
UNDETERMINED_DATA_NEEDS: dict[str, str] = {
    "critical_path": "先行・後続の依存関係と、計画終了日の入ったWBSタスクが必要です。",
    "silent_fire": "課題の更新履歴と、担当・期限の入った課題が必要です。",
    "change_frequency": "基準期間に登録された変更要求の履歴が必要です。",
    "defect_rate": "検出工程が入力された不具合と、比較できる件数の実績が必要です。",
}

#: 種別が未知のときの説明。空欄にすると「なぜ判定できないのか」が消える。
DEFAULT_DATA_NEED = "判定に使う実績データが不足しています。対象の登録内容を確認してください。"


#: 行き先が無いことを表す文言。偽のリンクを置かないための共通ラベル（UXP-12 / UXP-13）。
NO_LEDGER_LABEL = "確認先なし"


def _optional_url(name: str, query: str = "") -> str:
    """URL 名が解決できるときだけ URL を返す。解決できなければ空文字。

    存在しない画面へリンクすると押した先で 404 になる。行き先が無いことは
    空文字で表し、テンプレート側で「確認先なし」と文字で伝える。
    """

    try:
        url = reverse(name)
    except NoReverseMatch:
        return ""

    return f"{url}?{query}" if query else url


#: UXP-06: 品質指標の内部キーを業務用語へ言い換える。metric_label があればそちらを優先する。
QUALITY_METRIC_LABELS: dict[str, str] = {
    "defect_density": "不具合密度（規模あたりの不具合数）",
    "defect_rate": "不具合発生率",
    "test_pass_rate": "テスト合格率",
    "test_coverage": "テスト網羅率",
    "code_coverage": "コード網羅率",
    "review_rate": "レビュー実施率",
    "review_coverage": "レビュー実施率",
    "rework_rate": "手戻り率",
    "open_defect_rate": "未解決不具合率",
}


@dataclass(frozen=True)
class QualityMetricRow:
    """UXP-06: 品質指標 1 行。内部キーのままでは何の指標か伝わらない。"""

    metric: object
    label: str
    metric_key: str
    tone: str
    gate_label: str
    is_failed: bool
    is_unmeasured: bool
    next_data: str


def _quality_metric_label(metric) -> str:
    """業務で使う指標名。登録済みの表示名 → 既知キーの訳 → キーの順で決める。"""

    return metric.metric_label or QUALITY_METRIC_LABELS.get(metric.metric_key, metric.metric_key)


def _quality_next_data(metric) -> str:
    """合否を出せない指標に、次に入力／取得すべきデータを一文で書く。"""

    if metric.threshold is None and metric.target_value is None:
        return "品質ゲートの閾値と目標値を登録してください。どちらも無いため合否を判定できません。"

    if metric.threshold is None:
        return "品質ゲートの閾値を登録してください。閾値が無いため合否を判定できません。"

    return "この指標の最新の計測値を取得して登録してください。"


def _quality_rows(report) -> tuple[QualityMetricRow, ...]:
    rows: list[QualityMetricRow] = []

    for row in report.metric_rows:
        passes = row.metric.passes_gate
        rows.append(
            QualityMetricRow(
                metric=row.metric,
                label=_quality_metric_label(row.metric),
                metric_key=row.metric.metric_key,
                tone=row.tone,
                gate_label=row.gate_label,
                is_failed=passes is False,
                is_unmeasured=passes is None,
                next_data=_quality_next_data(row.metric) if passes is None else "",
            )
        )

    return tuple(rows)


#: UXP-09: リスク一覧のクイックビュー。既存の GET 条件（status）だけを使うので、
#: 「すべて」で必ず全件へ戻れる。ここに新しい絞り込み軸を足さない。
RISK_QUICK_VIEWS: tuple[tuple[str, str], ...] = (
    (Risk.Status.MATERIALIZED.value, "顕在化"),
    (Risk.Status.MITIGATING.value, "対応中"),
)


@dataclass(frozen=True)
class RiskQuickView:
    label: str
    query: str
    is_active: bool


def _risk_quick_views(status: str) -> tuple[RiskQuickView, ...]:
    return tuple(
        RiskQuickView(label=label, query=f"status={value}", is_active=status == value)
        for value, label in RISK_QUICK_VIEWS
    )


def _risks_without_due(rows) -> tuple:
    """期限が無い高スコアリスク。誰も気付かないまま放置されるので別枠で警告する。"""

    return tuple(row for row in rows if row.tone == "r" and row.risk.due_date is None)


def _risks_without_mitigation(rows) -> tuple:
    """対応方針が未記入のリスク。状態では絞り込めないので画面内に一覧を出す。"""

    return tuple(row for row in rows if not row.has_mitigation)


#: UXP-12: 指標ごとの確認先。台帳が無いものは None にして「確認先なし」と出す。
KPI_LEDGERS: dict[str, tuple[str, str, str] | None] = {
    KpiMeasurement.Kind.REPORT_HOURS.value: None,
    KpiMeasurement.Kind.CORRECTION_RATE.value: ("documents:list", "", "文書台帳を見る"),
    KpiMeasurement.Kind.FACT_ERROR_COUNT.value: ("audit:feedback_list", "", "フィードバック一覧を見る"),
    KpiMeasurement.Kind.DETECTION_LEAD_DAYS.value: ("dashboard:detection", "", "予兆検知を見る"),
}

#: UXP-12: 目標未設定は「未達」ではない。設定方法が分からないと放置されるので明記する。
KPI_TARGET_SETUP_NOTE = (
    "目標値は KPI 計測データの「目標値」欄に登録します。値を決めるのは効果測定の担当者です。"
    "目標が無い指標は、達成／未達のどちらとも判定していません。"
)


@dataclass(frozen=True)
class KpiViewRow:
    """UXP-12: KPI 1 行。改善率の向き・単位と、次に見る台帳を添える。"""

    row: object
    direction_label: str
    improvement_label: str
    unit_label: str
    url: str
    link_label: str
    is_unachieved: bool
    is_target_missing: bool


def _kpi_link(kind: str) -> tuple[str, str]:
    """指標から (URL, リンク文言) を作る。行き先が無ければ空 URL と「確認先なし」。"""

    destination = KPI_LEDGERS.get(kind)

    if destination is None:
        return ("", NO_LEDGER_LABEL)

    name, query, label = destination
    url = _optional_url(name, query)

    return (url, label) if url else ("", NO_LEDGER_LABEL)


def _improvement_label(improvement: int | None) -> str:
    if improvement is None:
        return "算出不能"

    if improvement > 0:
        return "良い方向"

    if improvement < 0:
        return "悪い方向"

    return "変化なし"


def _kpi_rows(report) -> tuple[KpiViewRow, ...]:
    rows: list[KpiViewRow] = []

    for row in report.rows:
        url, link_label = _kpi_link(row.measurement.kind)
        rows.append(
            KpiViewRow(
                row=row,
                direction_label="低いほど良い" if row.lower_is_better else "高いほど良い",
                improvement_label=_improvement_label(row.improvement_percent),
                unit_label=row.measurement.unit or "単位未設定",
                url=url,
                link_label=link_label,
                is_unachieved=row.target_achieved is False,
                is_target_missing=row.target_achieved is None,
            )
        )

    return tuple(rows)


@dataclass(frozen=True)
class KpiMissingRow:
    """UXP-12: 未計測の指標。未達と同じ扱いにすると、対処が変わってしまう。"""

    label: str
    url: str
    link_label: str


def _kpi_missing_rows(report) -> tuple[KpiMissingRow, ...]:
    measured = {row.measurement.kind for row in report.rows}
    rows: list[KpiMissingRow] = []

    for value, label in KpiMeasurement.Kind.choices:
        if value in measured:
            continue

        url, link_label = _kpi_link(value)
        rows.append(KpiMissingRow(label=label, url=url, link_label=link_label))

    return tuple(rows)


#: UXP-13: 判定不能のときに必要なデータと、その取得先。
POC_DATA_SOURCES: tuple[tuple[str, str, str, str], ...] = (
    (
        "report_hours",
        "レポート作成の作業時間（導入前の基準値と導入後の実績値）",
        "dashboard:kpi",
        "KPI・効果測定で登録状況を見る",
    ),
    (
        "correction_rate",
        "レビューでの修正量（赤字率）の基準値と実績値",
        "dashboard:kpi",
        "KPI・効果測定で登録状況を見る",
    ),
    (
        "fact_error",
        "生成物への事実誤認の指摘（フィードバック）",
        "audit:feedback_list",
        "フィードバック一覧を見る",
    ),
    (
        "detection_lead",
        "予兆検知のアラートと、対応した課題の記録",
        "dashboard:detection",
        "予兆検知を見る",
    ),
    (
        "hitl",
        "承認をブロックされた成果物",
        "documents:deliverables",
        "成果物一覧を見る",
    ),
)

#: 種別が未知のときの説明。空欄にすると「何を用意すればよいか」が消える。
POC_DEFAULT_DATA_NEED = "この受け入れ条件の判定に使う実績データ"


@dataclass(frozen=True)
class PocUnknownRow:
    """UXP-13: 判定不能 1 件。必要なデータと取得先を必ず添える。"""

    item: object
    data_need: str
    url: str
    link_label: str


def _poc_unknown_row(item) -> PocUnknownRow:
    for prefix, data_need, name, label in POC_DATA_SOURCES:
        if item.key.startswith(prefix):
            url = _optional_url(name)

            return PocUnknownRow(
                item=item,
                data_need=data_need,
                url=url,
                link_label=label if url else NO_LEDGER_LABEL,
            )

    return PocUnknownRow(
        item=item, data_need=POC_DEFAULT_DATA_NEED, url="", link_label=NO_LEDGER_LABEL
    )


def _poc_unknown_rows(report) -> tuple[PocUnknownRow, ...]:
    return tuple(_poc_unknown_row(item) for item in report.criteria if item.is_unknown)


def _poc_failed_rows(report) -> tuple:
    return tuple(item for item in report.criteria if item.verdict == VERDICT_FAIL)


@dataclass(frozen=True)
class AlertRow:
    """重要アラート 1 行。台帳へ着地できない行は、読めるだけで終わる。"""

    rank: int
    alert: Alert
    tone: str
    url: str
    link_label: str


@dataclass(frozen=True)
class NextAction:
    """いま最優先の 1 件。対象・理由・期限・遷移先を必ず揃える。

    複数並べると「どれから」を利用者が決め直すことになるので、1 件だけ返す。
    """

    target: str
    reason: str
    due_label: str
    url: str
    link_label: str


@dataclass(frozen=True)
class DetectionRow:
    """検知結果 1 行。対象の台帳へ進めないと、確認して終わりになる。"""

    label: str
    finding: Finding
    url: str
    link_label: str


@dataclass(frozen=True)
class SkipRow:
    """見送り 1 行。判定不能のときだけ、必要なデータを一文で添える。"""

    label: str
    skip: Skip
    data_need: str


def _ledger_link(category: str) -> tuple[str, str]:
    """分類から (URL, リンク文言) を作る。色ではなく文字で行き先を示す。"""

    name, query, label = LEDGER_DESTINATIONS.get(category, DEFAULT_DESTINATION)
    url = reverse(name)

    return (f"{url}?{query}" if query else url, label)


def _data_need(skip: Skip) -> str:
    """判定不能の見送りに、必要なデータの説明を付ける。"""

    if not skip.is_undetermined:
        return ""

    return UNDETERMINED_DATA_NEEDS.get(skip.kind, DEFAULT_DATA_NEED)


def _due_label(task: WbsTask) -> str:
    return f"{task.planned_end:%Y-%m-%d}" if task.planned_end else "期限未設定"


def _alert_rows(overview: Overview) -> tuple[AlertRow, ...]:
    rows: list[AlertRow] = []

    for ranked in overview.ranked_alerts:
        url, link_label = _ledger_link(ranked.alert.category)
        rows.append(
            AlertRow(
                rank=ranked.rank,
                alert=ranked.alert,
                tone=ranked.tone,
                url=url,
                link_label=link_label,
            )
        )

    return tuple(rows)


def _next_action(projects, overview: Overview, alert_rows: tuple[AlertRow, ...]) -> NextAction | None:
    """最優先の 1 件。止まっているもの → 遅れているもの → 判断待ち → 未対応アラートの順。

    先頭に置く 1 件は「読む対象」ではなく「今すぐ開く対象」なので、
    可能な限り個別のタスクまで降りる。何も無ければ None を返し、画面は安心状態を出す。
    """

    blocked = selectors.blocked_tasks_for(projects).first()

    if blocked is not None:
        return NextAction(
            target=f"{blocked.project.name} ／ {blocked.name}",
            reason=f"ブロック中で着手できません（ボール保持 {blocked.ball_holder or '未設定'}）",
            due_label=_due_label(blocked),
            url=reverse("projects:task_detail", args=[blocked.pk]),
            link_label="このタスクを開く",
        )

    delayed = selectors.delay_candidate_tasks_for(projects).first()

    if delayed is not None:
        return NextAction(
            target=f"{delayed.project.name} ／ {delayed.name}",
            reason=f"期限に対して進捗が {delayed.progress_percent:.0f}% しかありません",
            due_label=_due_label(delayed),
            url=reverse("projects:task_detail", args=[delayed.pk]),
            link_label="このタスクを開く",
        )

    if overview.proposal_count:
        return NextAction(
            target=f"AI介入提案 {overview.proposal_count}件",
            reason="人が判断するまで採用されません",
            due_label="期限なし（判断待ち）",
            url=reverse("dashboard:intervention"),
            link_label="提案を判断する",
        )

    if alert_rows:
        row = alert_rows[0]

        return NextAction(
            target=row.alert.title,
            reason=f"{row.alert.project.name} の未対応アラート（{row.alert.get_severity_display()}）",
            due_label=f"{row.alert.detected_at:%Y-%m-%d} 検知",
            url=row.url,
            link_label=row.link_label,
        )

    return None


@login_required
def control(request: HttpRequest) -> HttpResponse:
    projects = _projects(request)
    overview = build_overview(projects)
    alert_rows = _alert_rows(overview)

    return render(
        request,
        "pages/control_dashboard.html",
        {
            "overview": overview,
            # UXP-01: アラートは行き先つきで渡す。テンプレートで分岐させると、
            # 画面ごとに着地先がずれる。
            "alert_rows": alert_rows,
            "next_action": _next_action(projects, overview, alert_rows),
            "page_title": "プロジェクトダッシュボード",
        },
    )


@login_required
def detection(request: HttpRequest) -> HttpResponse:
    """検知結果の一覧。

    表示は必ず乾式実行（保存しない）で作る。「押す前に何が作られるか」が
    見えていないと、アラートが増えた理由を後から説明できない。
    """

    result = run_detection(_projects(request), dry_run=True)

    finding_rows: list[DetectionRow] = []

    for finding in result.findings:
        url, link_label = _ledger_link(finding.category)
        finding_rows.append(
            DetectionRow(
                label=kind_label(finding.kind),
                finding=finding,
                url=url,
                link_label=link_label,
            )
        )

    return render(
        request,
        "pages/detection_list.html",
        {
            "result": result,
            "finding_rows": tuple(finding_rows),
            "skip_rows": tuple(
                SkipRow(label=kind_label(skip.kind), skip=skip, data_need=_data_need(skip))
                for skip in result.skips
            ),
            "page_title": "予兆検知",
        },
    )


@login_required
@require_POST
def detection_run(request: HttpRequest) -> HttpResponse:
    """検知を実行してアラート・介入提案を保存する。

    参照ではなく作成なので POST のみ。対象は画面と同じ案件スコープに揃える。
    """

    result = run_detection(_projects(request))

    if result.alert_count:
        messages.success(
            request,
            f"検知を実行しました。アラート {result.alert_count}件、"
            f"介入提案 {result.proposal_count}件 を作成しました。",
        )
    else:
        messages.info(request, f"検知を実行しました。新しいアラートはありません（{result.summary_line()}）。")

    return redirect("dashboard:detection")


@login_required
def tasks(request: HttpRequest) -> HttpResponse:
    filters = TaskFilters(
        owner=request.GET.get("owner", "").strip(),
        status=request.GET.get("status", ""),
        priority=request.GET.get("priority", ""),
        due=request.GET.get("due", ""),
        progress=request.GET.get("progress", ""),
    )
    queryset = selectors.tasks_for(
        _projects(request),
        owner=filters.owner,
        status=filters.status,
        priority=filters.priority,
        due=filters.due,
        progress=filters.progress,
    )

    page = paginate(queryset, request)
    board = build_task_board(queryset, filters, page.object_list)
    is_gantt = request.GET.get("view", "") == "gantt"
    context = {
        "board": board,
        # 入力ルールの遵守状況（要件 #47）。絞り込みに関係なく案件全体で数える。
        # 絞り込むと「絞り込み条件の中では違反ゼロ」という無意味な数字になる。
        "input_rules": build_input_rule_report(_projects(request)),
        **_page_context(page, request),
        "page_title": "タスク一覧",
        "view_mode": "gantt" if is_gantt else "table",
        "view_query": _query_without_view(request),
    }

    if not is_gantt:
        return render(request, "pages/task_list.html", context)

    # 表と同じ行（絞り込み済み）をそのまま渡す。ここで別の QuerySet を引くと
    # 表とガントで見えるタスクが食い違う。
    context["chart"] = build_gantt_chart(board.rows, timezone.localdate())

    return render(request, "pages/task_gantt.html", context)


def _query_without_view(request: HttpRequest) -> str:
    """表示形式を切り替えるリンク用に、絞り込み条件だけを残した文字列。

    切り替えで条件が消えると、対象が変わったのか表示が変わったのか判別できない。
    """

    params = request.GET.copy()
    params.pop("view", None)
    params.pop("page", None)
    encoded = params.urlencode()

    return f"{encoded}&" if encoded else ""


@dataclass(frozen=True)
class CandidateRow:
    """介入候補 1 件。

    UXP-05: 遅延見込みとブロック中で並び順が変わると読み比べられないので、
    「遅延理由・担当・期限・次アクション」を同じ順・同じ語で持たせる。
    """

    task: WbsTask
    kind_label: str
    tone: str
    reason: str
    owner_label: str
    due_label: str
    next_action: str


def _delay_rows(tasks, today) -> tuple[CandidateRow, ...]:
    """遅延見込みタスクの 1 行要約。理由は期限との関係だけで決める（推測しない）。"""

    return tuple(
        CandidateRow(
            task=task,
            kind_label="遅延見込み",
            tone="r",
            reason=(
                "期限超過"
                if task.planned_end and task.planned_end < today
                else "期限接近で進捗不足"
            ),
            owner_label=task.owner or "未割当",
            due_label=_due_label(task),
            next_action=(
                "着手日を決めて担当に依頼する"
                if task.progress_percent == 0
                else "残作業を分割して期限を再設定する"
            ),
        )
        for task in tasks
    )


def _blocked_rows(tasks) -> tuple[CandidateRow, ...]:
    """ブロック中タスクの 1 行要約。次アクションは必ずボール保持者に向ける。"""

    return tuple(
        CandidateRow(
            task=task,
            kind_label="ブロック中",
            tone="a",
            reason="ブロック中で着手できない",
            owner_label=task.owner or "未割当",
            due_label=_due_label(task),
            next_action=f"ボール保持 {task.ball_holder or '未設定'} に解消を依頼する",
        )
        for task in tasks
    )


@login_required
def progress(request: HttpRequest) -> HttpResponse:
    projects = _projects(request)
    report = build_progress_report(
        projects,
        selectors.delay_candidate_tasks_for(projects),
        selectors.blocked_tasks_for(projects),
    )
    delay_rows = _delay_rows(report.delayed_tasks, timezone.localdate())
    blocked_rows = _blocked_rows(report.blocked_tasks)

    return render(
        request,
        "pages/progress.html",
        {
            "report": report,
            "delay_rows": delay_rows,
            "blocked_rows": blocked_rows,
            # UXP-05: 候補が 0 件のときに「AI介入提案へ」を主操作にすると、
            # 押した先が空になる。件数で主操作かどうかを決める。
            "candidate_count": len(delay_rows) + len(blocked_rows),
            # タスクの予実だけでは「節目に間に合うか」が分からない（要件 #4）。
            "milestones": build_milestone_report(projects),
            "page_title": "進捗予測・介入",
        },
    )


@login_required
def quality(request: HttpRequest) -> HttpResponse:
    projects = _projects(request)
    report = build_quality_report(
        selectors.quality_metrics_for(projects),
        selectors.defects_for(projects),
    )

    return render(
        request,
        "pages/quality.html",
        {
            "report": report,
            # 要件書とテスト計画書の要件IDを突き合わせる（要件 #44）。
            "coverage": build_coverage_report(
                documents_for(request.user, request.tenant)
            ),
            # UXP-06: 内部キーのままでは何の指標か伝わらない。行き先も一緒に渡す。
            "metric_rows": _quality_rows(report),
            "defect_list_url": _optional_url("projects:defect_list", "status=new"),
            "document_list_url": _optional_url("documents:list"),
            "page_title": "品質リアルタイム管理",
        },
    )


@login_required
def risk(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    queryset = selectors.risks_for(_projects(request), status=status)
    page = paginate(queryset, request)
    report = build_risk_report(queryset, page.object_list)

    return render(
        request,
        "pages/risk_list.html",
        {
            "report": report,
            "status": status,
            # UXP-09: 既存の絞り込み条件（status）だけで危険な対象へ 1 クリックで着地させる。
            "risk_quick_views": _risk_quick_views(status),
            "risk_no_due_rows": _risks_without_due(report.rows),
            "risk_no_mitigation_rows": _risks_without_mitigation(report.rows),
            **_page_context(page, request),
            "page_title": "リスク予測・対策",
        },
    )


#: UXP-10: 変更要求一覧のクイックビュー。既存の GET 条件（status）だけを使うので、
#: 「すべて」で必ず全件へ戻れる。ここに新しい絞り込み軸を足さない。
CHANGE_QUICK_VIEWS: tuple[tuple[str, str], ...] = (
    (ChangeRequest.Status.PENDING_APPROVAL.value, "判断待ち"),
    (ChangeRequest.Status.UNDER_REVIEW.value, "影響分析中"),
)


@dataclass(frozen=True)
class ChangeQuickView:
    label: str
    query: str
    is_active: bool


@dataclass(frozen=True)
class ChangeListRow:
    """変更要求一覧の 1 行。

    「この行を自分が判断できるか」は案件ロールまで見ないと決まらない
    （要件 #30、テナント側の承認権だけでは足りない）。テンプレートからは
    案件ごとの判定を呼べないため、ここで 1 行ずつ確定させる。
    """

    change: ChangeRequest
    tone: str
    is_pending: bool
    can_decide: bool
    denied_reason: str
    missing_labels: tuple[str, ...]

    @property
    def has_missing(self) -> bool:
        return bool(self.missing_labels)


def _missing_change_inputs(change: ChangeRequest) -> tuple[str, ...]:
    """判断に要る 3 項目のうち空欄のもの。

    0 は「影響なし」という入力なので、空欄（None）と区別する。
    """

    candidates = (
        ("工数", change.estimated_effort_days is None),
        ("日程影響", change.schedule_impact_days is None),
        ("影響範囲", not change.impact_scope),
    )

    return tuple(label for label, is_missing in candidates if is_missing)


def _change_quick_views(status: str) -> tuple[ChangeQuickView, ...]:
    return tuple(
        ChangeQuickView(label=label, query=f"status={value}", is_active=status == value)
        for value, label in CHANGE_QUICK_VIEWS
    )


def _change_list_rows(report_rows, user) -> tuple[ChangeListRow, ...]:
    """行ごとに判断可否と未入力項目を添える。

    権限判定は案件単位なので、同じ案件の結果を使い回して問い合わせを 1 案件 1 回に抑える。
    """

    verdicts: dict[int, tuple[bool, str]] = {}
    rows: list[ChangeListRow] = []

    for row in report_rows:
        project = row.change.project

        if project.pk not in verdicts:
            verdicts[project.pk] = (
                can_approve_in_project(user, project),
                approval_denied_reason(user, project),
            )

        can_approve, denied_reason = verdicts[project.pk]
        rows.append(
            ChangeListRow(
                change=row.change,
                tone=row.tone,
                is_pending=row.is_pending,
                can_decide=row.is_pending and can_approve,
                denied_reason=denied_reason,
                missing_labels=_missing_change_inputs(row.change),
            )
        )

    return tuple(rows)


@login_required
def change(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    queryset = selectors.change_requests_for(_projects(request), status=status)
    page = paginate(queryset, request)
    report = build_change_report(queryset, page.object_list)

    return render(
        request,
        "pages/change_list.html",
        {
            "report": report,
            "status": status,
            "change_rows": _change_list_rows(report.rows, request.user),
            "change_quick_views": _change_quick_views(status),
            **_page_context(page, request),
            "page_title": "変更影響分析",
        },
    )


@login_required
def intervention(request: HttpRequest) -> HttpResponse:
    """AI 介入提案の一覧。

    ここでは採否を記録しない（UXP-11）。一覧は「どれを先に見るか」を決める場所で、
    判断は根拠を並べた専用画面で行う。一覧に判断フォームを置くと、根拠を読まないまま
    採用できてしまい、根拠追跡という前提が形だけになる。
    """

    status = request.GET.get("status", "")
    queryset = selectors.interventions_for(_projects(request), status=status)
    page = paginate(queryset, request)
    report = build_intervention_report(queryset, page.object_list)
    pending_status = InterventionProposal.Status.PROPOSED

    return render(
        request,
        "pages/intervention_list.html",
        {
            "report": report,
            "status": status,
            # 根拠のない候補は、判断画面へのリンクより先に警告として出す。
            # 表の中の小さな印だけでは、そのまま判断へ進まれてしまう。
            "evidence_gap_rows": [
                row
                for row in report.rows
                if row.proposal.status == pending_status and not row.evidence_items
            ],
            "pending_status": pending_status,
            **_page_context(page, request),
            "page_title": "AI介入提案",
        },
    )


@login_required
def kpi(request: HttpRequest) -> HttpResponse:
    """KPI・効果測定。

    実測値が1件も無いと画面が空になり、導入前後の比較ができない。
    そのときだけ WBS・課題・不具合から算出した代替指標を出す。
    実測と混同させないため、テンプレート側で「実測ではない」ことを明示する。
    """

    projects = _projects(request)
    report = build_kpi_report(selectors.kpi_measurements_for(projects))

    return render(
        request,
        "pages/kpi.html",
        {
            "report": report,
            "derived_rows": build_derived_rows(projects) if not report.rows else (),
            # UXP-12: 未達と未計測は対処が違う。分けて渡し、行き先も添える。
            "kpi_rows": _kpi_rows(report),
            "kpi_missing_rows": _kpi_missing_rows(report),
            "kpi_target_setup_note": KPI_TARGET_SETUP_NOTE,
            "page_title": "KPI・効果測定",
        },
    )


@login_required
def poc(request: HttpRequest) -> HttpResponse:
    """PoC 受け入れ条件の合否判定。

    KPI 画面は数値を出すだけで「PoC が成功したか」を言わない。ここでは目標値と
    突き合わせて合否を出す。テナント分離は案件・フィードバックの両方で必要なので、
    それぞれの selectors を入口に通したものだけをサービスへ渡す。
    """

    report = build_poc_evaluation(
        _projects(request),
        feedbacks_for(request.user, getattr(request, "tenant", None)),
    )

    return render(
        request,
        "pages/poc_evaluation.html",
        {
            "report": report,
            "business_day_note": BUSINESS_DAY_NOTE,
            # UXP-13: 総合判定だけでは次の一手が決まらない。不合格と判定不能を抜き出す。
            "failed_rows": _poc_failed_rows(report),
            "unknown_rows": _poc_unknown_rows(report),
            "page_title": "PoC合否判定",
        },
    )


@login_required
def intervention_decide(request: HttpRequest, pk) -> HttpResponse:
    """AI 介入提案に人の判断を記録する。

    対象は必ず「参照できる案件に紐づく提案」に絞る。テナントを越えた ID を
    直接叩かれても 404 になるよう、取得の時点で候補を限定している。
    """

    proposal = get_object_or_404(
        InterventionProposal.objects.select_related("project", "project__tenant", "alert"),
        pk=pk,
        project__in=_projects(request),
    )

    # 介入提案の採否は人の判断（HITL）そのもので、変更要求の判断と同じ重さがある。
    # 参照専用の利用者に記録させると、誰が決めたのかという監査の前提が崩れる。
    if not can_approve_in_project(request.user, proposal.project):
        raise PermissionDenied(
            approval_denied_reason(request.user, proposal.project)
            or "この提案を判断する権限がありません。"
        )

    if not is_pending(proposal):
        messages.warning(request, "この提案はすでに判断済みです。履歴を保つため再判断はできません。")

        return redirect("dashboard:intervention")

    form = InterventionDecisionForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            decided = decide_intervention(
                proposal,
                user=request.user,
                status=form.cleaned_data["status"],
                decision_reason=form.cleaned_data["decision_reason"],
                modified_action=form.cleaned_data["modified_action"],
            )
        except AlreadyDecidedError:
            messages.warning(request, "他の利用者が先に判断しました。最新の状態を確認してください。")
        else:
            messages.success(
                request,
                f"「{decided.title}」を{decided.get_status_display()}として記録しました。",
            )

        return redirect("dashboard:intervention")

    # 根拠と信頼度は一覧と同じ整形を通す。画面ごとに整形すると、一覧で「根拠あり」
    # に見えたものが判断画面では消える、といった食い違いが起きる。
    row = InterventionRow(proposal=proposal)

    return render(
        request,
        "pages/intervention_form.html",
        {
            "form": form,
            "proposal": proposal,
            "evidence_items": row.evidence_items,
            "confidence_percent": row.confidence_percent,
            "page_title": "AI介入提案の判断",
        },
    )
