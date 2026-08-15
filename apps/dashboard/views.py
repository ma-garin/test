"""管制ダッシュボード配下の画面。

参照は `apps.dashboard.selectors`、集計は `apps.dashboard.services` に置き、
ビューは「絞り込み条件を受け取って、組み立て済みの表示データを渡す」だけにする。
テナント分離は `projects_for()` を必ず入口に通すことで担保している。
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.constants import Action
from apps.accounts.services import permissions
from apps.audit.selectors import feedbacks_for
from apps.core.pagination import page_window, paginate, query_without_page
from apps.dashboard import selectors
from apps.dashboard.forms import InterventionDecisionForm
from apps.dashboard.models import InterventionProposal
from apps.dashboard.services.decisions import (
    build_change_report,
    build_intervention_report,
    build_risk_report,
)
from apps.dashboard.services.detection import kind_label, run_detection
from apps.dashboard.services.earned_value import build_portfolio
from apps.dashboard.services.gantt import build_gantt_chart
from apps.dashboard.services.interventions import (
    AlreadyDecidedError,
    decide_intervention,
    is_pending,
)
from apps.dashboard.services.kpi import build_derived_rows, build_kpi_report
from apps.dashboard.services.milestones import build_milestone_report
from apps.dashboard.services.ops_rules import build_ops_rules_report
from apps.dashboard.services.overview import build_overview
from apps.dashboard.services.poc_evaluation import BUSINESS_DAY_NOTE, build_poc_evaluation
from apps.dashboard.services.progress import build_progress_report
from apps.dashboard.services.quality import build_quality_report
from apps.dashboard.services.tasks import TaskFilters, build_task_board
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


@login_required
def control(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "pages/control_dashboard.html",
        {"overview": build_overview(_projects(request)), "page_title": "管制ダッシュボード"},
    )


@login_required
def detection(request: HttpRequest) -> HttpResponse:
    """検知結果の一覧。

    表示は必ず乾式実行（保存しない）で作る。「押す前に何が作られるか」が
    見えていないと、アラートが増えた理由を後から説明できない。
    """

    result = run_detection(_projects(request), dry_run=True)

    return render(
        request,
        "pages/detection_list.html",
        {
            "result": result,
            "finding_rows": [(kind_label(f.kind), f) for f in result.findings],
            "skip_rows": [(kind_label(s.kind), s) for s in result.skips],
            "page_title": "予兆検知",
        },
    )


@login_required
@require_POST
def detection_run(request: HttpRequest) -> HttpResponse:
    """検知を実行してアラート・介入提案を保存する。

    参照ではなく作成なので POST のみ。対象は画面と同じ案件スコープに揃える。
    アラートと介入提案を書き込む操作なので、参照だけの利用者には実行させない。
    案件が選択されていればその案件の役割で、未選択ならテナント単位で判定する。
    """

    permissions.require(request.user, Action.EDIT, getattr(request, "project", None))

    # 判定を通っても、編集権限のある案件だけを対象にする。案件未選択のときに
    # 「参照だけの案件」へアラートを書き込まないため。
    targets = [
        project
        for project in _projects(request)
        if permissions.can(request.user, Action.EDIT, project)
    ]
    result = run_detection(targets)

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


@login_required
def progress(request: HttpRequest) -> HttpResponse:
    projects = _projects(request)
    report = build_progress_report(
        projects,
        selectors.delay_candidate_tasks_for(projects),
        selectors.blocked_tasks_for(projects),
    )

    return render(
        request,
        "pages/progress.html",
        {
            "report": report,
            # 進捗率だけでは「いつ終わるか」に答えられない。工数から出来高を出す。
            "earned_values": build_portfolio(projects, timezone.localdate()),
            # タスク単位の遅れより先に、対外的な約束（マイルストーン）の予実を見せる。
            "milestones": build_milestone_report(selectors.milestones_for(projects)),
            "page_title": "進捗予測・介入",
        },
    )


@login_required
def ops_rules(request: HttpRequest) -> HttpResponse:
    """入力標準ルールの運用支援。

    集計より前に「そもそもデータが更新されているか」を見る画面。
    催促は人単位でしか行えないため、担当者別の未更新一覧を主役にする。
    """

    report = build_ops_rules_report(selectors.ops_rule_tasks_for(_projects(request)))

    return render(
        request,
        "pages/ops_rules.html",
        {"report": report, "page_title": "入力標準ルール運用"},
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
        {"report": report, "page_title": "品質リアルタイム管理"},
    )


@login_required
def risk(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    queryset = selectors.risks_for(_projects(request), status=status)
    page = paginate(queryset, request)

    return render(
        request,
        "pages/risk_list.html",
        {
            "report": build_risk_report(queryset, page.object_list),
            "status": status,
            **_page_context(page, request),
            "page_title": "リスク予測・対策",
        },
    )


@login_required
def change(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    queryset = selectors.change_requests_for(_projects(request), status=status)
    page = paginate(queryset, request)

    return render(
        request,
        "pages/change_list.html",
        {
            "report": build_change_report(queryset, page.object_list),
            "status": status,
            **_page_context(page, request),
            "page_title": "変更影響分析",
        },
    )


@login_required
def intervention(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    queryset = selectors.interventions_for(_projects(request), status=status)
    page = paginate(queryset, request)

    return render(
        request,
        "pages/intervention_list.html",
        {
            "report": build_intervention_report(queryset, page.object_list),
            "status": status,
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
        _projects(request), feedbacks_for(request.user, getattr(request, "tenant", None))
    )

    return render(
        request,
        "pages/poc_evaluation.html",
        {
            "report": report,
            "business_day_note": BUSINESS_DAY_NOTE,
            "page_title": "PoC評価・合否判定",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def intervention_decide(request: HttpRequest, pk) -> HttpResponse:
    """AI 介入提案に人の判断を記録する。

    対象は必ず「参照できる案件に紐づく提案」に絞る。テナントを越えた ID を
    直接叩かれても 404 になるよう、取得の時点で候補を限定している。

    採否の確定は承認そのものなので、案件内の役割で承認権限を確かめる。
    判断フォームは GET でも同じ判定を通す（押してから断られる導線にしない）。
    """

    proposal = get_object_or_404(
        InterventionProposal.objects.select_related("project", "project__tenant", "alert"),
        pk=pk,
        project__in=_projects(request),
    )
    permissions.require(request.user, Action.APPROVE, proposal)

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

    return render(
        request,
        "pages/intervention_form.html",
        {
            "form": form,
            "proposal": proposal,
            "page_title": "AI介入提案の判断",
        },
    )
