"""案件一覧・詳細と、WBS タスクの登録・編集・アーカイブ。

タスクの取得は必ず `projects_for` で絞った案件配下に限定する。テナント越境は
「見えない」ではなく「存在しない（404）」として扱い、ID の総当たりで他テナントの
存在有無が漏れないようにする。
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import BooleanField, Count, ExpressionWrapper, Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.pagination import page_window, paginate, query_without_page
from apps.integrations.selectors import synced_records_for
from apps.projects.forms import (
    ChangeDecisionForm,
    ChangeRequestForm,
    DefectForm,
    IssueForm,
    RiskForm,
    RiskPromoteForm,
    WbsTaskForm,
)
from apps.projects.models import ChangeRequest, Defect, Issue, Project, Risk, Severity, WbsTask
from apps.projects.permissions import (
    approval_denied_reason,
    can_approve_in_project,
    can_edit_project,
    editable_projects_for,
)
from apps.projects.selectors import projects_for, scoped_projects_for
from apps.projects.services.change_requests import decide_change_request, save_change_request
from apps.projects.services.defects import close_defect, save_defect
from apps.projects.services.issues import close_issue, save_issue
from apps.projects.services.risks import close_risk, promote_risk_to_issue, save_risk
from apps.projects.services.tasks import archive_task, create_task, update_task
from apps.rag.services.similar_projects import similar_projects_for

TASK_LIST_URL = "dashboard:tasks"
RISK_LIST_URL = "dashboard:risk"
ISSUE_LIST_URL = "projects:issue_list"

#: 課題一覧の期限フィルタ。日付そのものではなく「今どう困っているか」で選ばせる。
ISSUE_DUE_CHOICES: tuple[tuple[str, str], ...] = (
    ("overdue", "期限超過"),
    ("soon", "期限接近（7日以内）"),
    ("none", "期限なし"),
)

#: まだ対処が終わっていない課題の状態。解決・完了に「期限超過」を出すと、
#: 対処済みの行まで危険に見え、本当に遅れている行が埋もれる。
ISSUE_OPEN_STATUSES: tuple[str, ...] = (
    Issue.Status.OPEN,
    Issue.Status.IN_PROGRESS,
    Issue.Status.BLOCKED,
)

#: 不具合のクイックビュー「未解決かつ重大」の定義。
DEFECT_QUICK_UNRESOLVED_CRITICAL = "unresolved_critical"
DEFECT_QUICK_LABEL = "未解決かつ重大"
DEFECT_UNRESOLVED_STATUSES: tuple[str, ...] = (
    Defect.Status.NEW,
    Defect.Status.ANALYZING,
    Defect.Status.FIXING,
    Defect.Status.VERIFYING,
)
DEFECT_CRITICAL_SEVERITIES: tuple[str, ...] = (Severity.HIGH, Severity.CRITICAL)


def _selected_value(request: HttpRequest, key: str, choices) -> str:
    """GET の値のうち、選択肢にあるものだけを採用する。

    URL を手で編集された程度で「0 件だが理由が分からない」画面にしない。
    """

    value = request.GET.get(key, "")

    return value if any(value == choice for choice, _ in choices) else ""


def _choice_label(value: str, choices) -> str:
    return next((label for choice, label in choices if choice == value), value)


def _return_to(request: HttpRequest, fallback_url_name: str) -> str:
    """一覧から渡された戻り先だけを、同一ホスト内に限って採用する。"""

    candidate = request.POST.get("next") or request.GET.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate

    return reverse(fallback_url_name)


@login_required
def project_list(request: HttpRequest) -> HttpResponse:
    """案件一覧。総件数はページャに出し、表示だけをページで切る。"""

    # 案件一覧だけは選択中の案件で絞らない。ここは切替の起点なので、
    # 絞った状態だと他の案件へ移れなくなる。
    #
    # UXP-30: 「未解決課題」は行ごとに count() を呼ぶと案件数だけクエリが増えるため、
    # annotate で 1 クエリにまとめる。推測値は出さず、実データのある列だけを足す。
    projects = projects_for(request.user, request.tenant).annotate(
        open_issue_count=Count(
            "issue_set",
            filter=Q(issue_set__status__in=ISSUE_OPEN_STATUSES),
            distinct=True,
        )
    )
    # 集計を付けると Meta.ordering が効かなくなる（Django の仕様）。
    # 並び順が消えるとページ送りで同じ行が二度出るため、ここで明示する。
    page = paginate(projects.order_by("code"), request)

    return render(
        request,
        "pages/project_list.html",
        {
            "projects": page.object_list,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            # 0 件のとき「案件が無い」のか「権限が無い」のかを区別して伝えるため、
            # テナント全体の件数も渡す。件数だけなので情報漏洩にはならない。
            "tenant_project_count": Project.objects.alive()
            .filter(tenant=request.tenant)
            .count()
            if request.tenant
            else 0,
            "page_title": "案件一覧",
        },
    )


@login_required
def project_detail(request: HttpRequest, pk) -> HttpResponse:
    project = get_object_or_404(projects_for(request.user, request.tenant), pk=pk)

    return render(
        request,
        "pages/project_detail.html",
        {
            "project": project,
            # 候補は必ず参照権限のある案件だけに絞る（テナント越境の防止）。
            "similar_projects": similar_projects_for(request, project),
            # 誰がこの案件で何をできるかを 1 表で見せる（要件 #30）。
            "members": _member_rows(project),
            # 台帳を読む前に「次に何をするか」を 1 件だけ出す（UXP-31）。
            "next_action": _project_next_action(project),
            "page_title": project.name,
        },
    )


def _date_text(value) -> str:
    return f"{value:%Y/%m/%d}" if value else "未設定"


def _task_action(task, *, reason: str, tone: str) -> dict:
    return {
        "reason": reason,
        "tone": tone,
        "title": task.name,
        "meta": (
            f"WBS {task.wbs_code} ／ 期限 {_date_text(task.planned_end)}"
            f" ／ 担当 {task.owner or '未設定'}"
        ),
        "url": reverse("projects:task_detail", args=[task.pk]),
    }


def _overdue_action(project, today) -> dict | None:
    """期限を過ぎている対象。タスクと課題のうち、より古い期限のものを選ぶ。"""

    task = (
        project.wbstask_set.exclude(status__in=(WbsTask.Status.DONE, WbsTask.Status.ARCHIVED))
        .filter(planned_end__lt=today)
        .order_by("planned_end")
        .first()
    )
    issue = (
        project.issue_set.filter(due_date__lt=today, status__in=ISSUE_OPEN_STATUSES)
        .order_by("due_date")
        .first()
    )

    candidates: list[tuple] = []

    if task is not None:
        candidates.append((task.planned_end, _task_action(task, reason="期限超過", tone="r")))

    if issue is not None:
        candidates.append(
            (
                issue.due_date,
                {
                    "reason": "期限超過",
                    "tone": "r",
                    "title": issue.title,
                    "meta": (
                        f"課題 ／ 期限 {_date_text(issue.due_date)}"
                        f" ／ 担当 {issue.owner or '未設定'}"
                    ),
                    # 課題は詳細画面を持たないので、絞り込み済みの一覧へ送る。
                    "url": f"{reverse(ISSUE_LIST_URL)}?due=overdue",
                },
            )
        )

    if not candidates:
        return None

    return min(candidates, key=lambda candidate: candidate[0])[1]


def _blocked_action(project, today) -> dict | None:
    """着手できないまま止まっている対象。期限が近いものから出す。"""

    task = (
        project.wbstask_set.filter(status=WbsTask.Status.BLOCKED)
        .order_by("planned_end", "wbs_code")
        .first()
    )

    return None if task is None else _task_action(task, reason="ブロック中", tone="a")


def _pending_decision_action(project, today) -> dict | None:
    """判断が返ってこないと先へ進めない変更要求。"""

    change = (
        project.changerequest_set.filter(
            status__in=(ChangeRequest.Status.UNDER_REVIEW, ChangeRequest.Status.PENDING_APPROVAL)
        )
        .order_by("created_at")
        .first()
    )

    if change is None:
        return None

    return {
        "reason": "判断待ち",
        "tone": "b",
        "title": change.title,
        "meta": f"変更要求 ／ {change.get_status_display()} ／ 起票 {change.requested_by or '不明'}",
        "url": reverse("dashboard:change"),
    }


def _project_next_action(project) -> dict | None:
    """この案件で次に対応すべきことを 1 件だけ返す（UXP-31）。

    期限超過 → ブロック中 → 判断待ち の順に見て、最初に当たったものを返す。
    並べて出すと「まず何をするか」の判断が利用者へ戻ってしまうため、件数は
    各カードに任せ、ここは 1 件に絞る。該当が無ければ None（安心状態）。
    """

    today = timezone.localdate()

    for build in (_overdue_action, _blocked_action, _pending_decision_action):
        action = build(project, today)

        if action is not None:
            return action

    return None


def _member_rows(project) -> list:
    """案件メンバーに、実際に行使できる権限を添えて返す（要件 #30）。

    ロール名だけを出しても「結局この人は承認できるのか」が読み取れない。
    判定関数そのものを通した結果を表示し、画面と実際の挙動をずらさない。
    """

    from apps.projects.models import ProjectMember

    members = list(
        ProjectMember.objects.filter(project=project)
        .select_related("user")
        .order_by("user__username")
    )

    for member in members:
        member.can_edit = can_edit_project(member.user, project)
        member.can_approve = can_approve_in_project(member.user, project)

    return members


# --- 変更要求・不具合 -------------------------------------------------------
# 取得は必ず「参照できる案件」に紐づくものへ絞る。絞り込みを個々のビューで
# 書くと必ずどこかで漏れるため、下の 2 つのヘルパー以外から直接 objects を
# 触らないこと。


def _change_requests_for(request: HttpRequest):
    return ChangeRequest.objects.filter(
        project__in=projects_for(request.user, request.tenant)
    ).select_related("project")


def _scoped(request: HttpRequest, queryset):
    """一覧の絞り込み。選択中の案件があればその1件だけにする。

    詳細・編集には掛けない。直リンクで開いたときに「権限はあるのに
    選択中でないから404」となるのは、実務では事故のもとになる。
    """

    return queryset.filter(project__in=scoped_projects_for(request))


def _defects_for(request: HttpRequest):
    return Defect.objects.filter(
        project__in=projects_for(request.user, request.tenant)
    ).select_related("project")


def _editable_projects(request: HttpRequest):
    """書き込みフォームで選べる案件（要件 #30）。

    参照専用に設定された案件は選択肢に出さない。出してから保存時に弾くと、
    利用者は「なぜ保存できないのか」を画面から判断できない。
    """

    return editable_projects_for(request.user, projects_for(request.user, request.tenant))


def _require_edit(request: HttpRequest, project) -> None:
    """案件のデータを更新できないなら 403 にする。"""

    if not can_edit_project(request.user, project):
        raise PermissionDenied("この案件のデータを編集する権限がありません。")


def _render_change_form(request: HttpRequest, *, change: ChangeRequest | None) -> HttpResponse:
    if change is not None:
        _require_edit(request, change.project)

    projects = _editable_projects(request)
    return_to = _return_to(request, "dashboard:change")

    if request.method == "POST":
        form = ChangeRequestForm(request.POST, instance=change, projects=projects)

        if form.is_valid():
            saved = save_change_request(form, user=request.user)
            messages.success(request, f"変更要求「{saved.title}」を保存しました。")

            return redirect(return_to)

        messages.error(request, "変更要求を保存できませんでした。入力内容を確認してください。")
    else:
        form = ChangeRequestForm(instance=change, projects=projects)

    title = "変更要求の編集" if change else "変更要求の新規作成"

    return render(
        request,
        "pages/change_form.html",
        {
            "form": form,
            "change": change,
            "form_title": title,
            "page_title": title,
            "return_to": return_to,
        },
    )


@login_required
def change_create(request: HttpRequest) -> HttpResponse:
    return _render_change_form(request, change=None)


@login_required
def change_edit(request: HttpRequest, pk) -> HttpResponse:
    return _render_change_form(request, change=get_object_or_404(_change_requests_for(request), pk=pk))


@login_required
def change_decide(request: HttpRequest, pk) -> HttpResponse:
    """変更要求の承認・却下。監査対象なので理由と判断者を必ず残す。"""

    change = get_object_or_404(_change_requests_for(request), pk=pk)
    return_to = _return_to(request, "dashboard:change")

    # 案件ロールまで見る（要件 #30）。テナント側で承認権があっても、
    # その案件で参照専用に設定されていれば判断させない。
    if not can_approve_in_project(request.user, change.project):
        raise PermissionDenied(
            approval_denied_reason(request.user, change.project)
            or "変更要求を判断する権限がありません。"
        )

    if request.method == "POST":
        form = ChangeDecisionForm(request.POST)

        if form.is_valid():
            try:
                decided = decide_change_request(
                    change,
                    user=request.user,
                    decision=form.cleaned_data["decision"],
                    reason=form.cleaned_data["reason"],
                )
            except ValidationError as error:
                messages.error(request, "; ".join(error.messages))
            else:
                messages.success(
                    request,
                    f"変更要求「{decided.title}」を{decided.get_status_display()}にしました。",
                )

                return redirect(return_to)
        else:
            messages.error(request, "判断を記録できませんでした。入力内容を確認してください。")
    else:
        form = ChangeDecisionForm()

    return render(
        request,
        "pages/change_decide_form.html",
        {
            "form": form,
            "change": change,
            "page_title": "変更要求の判断",
            "return_to": return_to,
        },
    )


@login_required
def defect_list(request: HttpRequest) -> HttpResponse:
    """不具合一覧。件数が増えても 1 画面へ詰め込まない。

    UXP-08: 状態・重大度・検出工程を GET で絞り込み、「未解決かつ重大」だけを
    1 クリックで開けるようにする。条件はすべて GET なので URL をそのまま共有でき、
    「条件をクリア」で必ず全件へ戻れる。
    """

    status_choices = Defect.Status.choices
    severity_choices = Severity.choices
    base = _scoped(request, _defects_for(request))
    # 検出工程は自由入力なので、選択肢は実際に登録されている値から作る。
    phase_choices = tuple(
        (phase, phase)
        for phase in base.exclude(phase="")
        .order_by("phase")
        .values_list("phase", flat=True)
        .distinct()
    )

    quick_raw = request.GET.get("quick", "")
    quick = quick_raw if quick_raw == DEFECT_QUICK_UNRESOLVED_CRITICAL else ""
    status = _selected_value(request, "status", status_choices)
    severity = _selected_value(request, "severity", severity_choices)
    phase = _selected_value(request, "phase", phase_choices)

    queryset = base
    applied: list[str] = []

    if quick:
        queryset = queryset.filter(
            status__in=DEFECT_UNRESOLVED_STATUSES,
            severity__in=DEFECT_CRITICAL_SEVERITIES,
        )
        applied.append(DEFECT_QUICK_LABEL)

    if status:
        queryset = queryset.filter(status=status)
        applied.append(f"状態: {_choice_label(status, status_choices)}")

    if severity:
        queryset = queryset.filter(severity=severity)
        applied.append(f"重大度: {_choice_label(severity, severity_choices)}")

    if phase:
        queryset = queryset.filter(phase=phase)
        applied.append(f"検出工程: {phase}")

    page = paginate(queryset, request)

    return render(
        request,
        "pages/defect_list.html",
        {
            "defects": page.object_list,
            "external_links": synced_records_for(page.object_list),
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "page_title": "不具合管理",
            "status_choices": status_choices,
            "severity_choices": severity_choices,
            "phase_choices": phase_choices,
            "filters": {
                "status": status,
                "severity": severity,
                "phase": phase,
                "quick": quick,
                "applied": applied,
            },
            "is_filtered": bool(applied),
            # 0 件のとき「未登録」と「絞り込み 0 件」を書き分けるための材料。
            "has_any": page.paginator.count > 0 or base.exists(),
            "total_count": page.paginator.count,
            "ordering_label": "登録が新しい順",
        },
    )


def _render_defect_form(request: HttpRequest, *, defect: Defect | None) -> HttpResponse:
    if defect is not None:
        _require_edit(request, defect.project)

    projects = _editable_projects(request)
    return_to = _return_to(request, "projects:defect_list")

    if request.method == "POST":
        form = DefectForm(request.POST, instance=defect, projects=projects)

        if form.is_valid():
            saved = save_defect(form, user=request.user)
            messages.success(request, f"不具合「{saved.title}」を保存しました。")

            return redirect(return_to)

        messages.error(request, "不具合を保存できませんでした。入力内容を確認してください。")
    else:
        form = DefectForm(instance=defect, projects=projects)

    title = "不具合の編集" if defect else "不具合の新規登録"

    return render(
        request,
        "pages/defect_form.html",
        {
            "form": form,
            "defect": defect,
            "form_title": title,
            "page_title": title,
            "return_to": return_to,
        },
    )


@login_required
def defect_create(request: HttpRequest) -> HttpResponse:
    return _render_defect_form(request, defect=None)


@login_required
def defect_edit(request: HttpRequest, pk) -> HttpResponse:
    return _render_defect_form(request, defect=get_object_or_404(_defects_for(request), pk=pk))


@login_required
@require_POST
def defect_close(request: HttpRequest, pk) -> HttpResponse:
    """不具合のクローズ。物理削除はせず状態で終了を表す。"""

    target = get_object_or_404(_defects_for(request), pk=pk)
    _require_edit(request, target.project)
    defect = close_defect(target, user=request.user)
    messages.success(request, f"不具合「{defect.title}」をクローズしました。")

    return redirect(_return_to(request, "projects:defect_list"))


def _tasks_for(request: HttpRequest) -> QuerySet[WbsTask]:
    """参照できる案件配下のタスクだけを返す。"""

    return WbsTask.objects.filter(
        project__in=projects_for(request.user, request.tenant)
    ).select_related("project")


@login_required
def task_create(request: HttpRequest) -> HttpResponse:
    projects = _editable_projects(request)
    return_to = _return_to(request, TASK_LIST_URL)

    if request.method == "POST":
        form = WbsTaskForm(request.POST, projects=projects)

        if form.is_valid():
            task = create_task(form)
            messages.success(request, f"タスク「{task.name}」を作成しました。")

            return redirect(return_to)

        messages.error(request, "入力内容を確認してください。")
    else:
        form = WbsTaskForm(projects=projects)

    return render(
        request,
        "pages/task_form.html",
        {
            "form": form,
            "task": None,
            "page_title": "タスクを新規作成",
            "return_to": return_to,
        },
    )


@login_required
def task_edit(request: HttpRequest, pk) -> HttpResponse:
    task = get_object_or_404(_tasks_for(request), pk=pk)
    _require_edit(request, task.project)
    projects = _editable_projects(request)
    return_to = _return_to(request, TASK_LIST_URL)

    if request.method == "POST":
        form = WbsTaskForm(request.POST, instance=task, projects=projects)

        if form.is_valid():
            update_task(form)
            messages.success(request, f"タスク「{task.name}」を更新しました。")

            return redirect(return_to)

        messages.error(request, "入力内容を確認してください。")
    else:
        form = WbsTaskForm(instance=task, projects=projects)

    return render(
        request,
        "pages/task_form.html",
        {
            "form": form,
            "task": task,
            "page_title": "タスクを編集",
            "return_to": return_to,
        },
    )


@login_required
def task_detail(request: HttpRequest, pk) -> HttpResponse:
    task = get_object_or_404(_tasks_for(request), pk=pk)
    return_to = _return_to(request, TASK_LIST_URL)

    return render(
        request,
        "pages/task_detail.html",
        {
            "task": task,
            "related_tasks": task.related_tasks.all(),
            "child_tasks": task.children.all(),
            "page_title": f"{task.wbs_code} {task.name}",
            "return_to": return_to,
        },
    )


@login_required
@require_POST
def task_archive(request: HttpRequest, pk) -> HttpResponse:
    task = get_object_or_404(_tasks_for(request), pk=pk)
    _require_edit(request, task.project)
    archive_task(task)
    messages.success(request, f"タスク「{task.name}」をアーカイブしました。")

    return redirect(_return_to(request, TASK_LIST_URL))


def _risks_for(request: HttpRequest) -> QuerySet[Risk]:
    """参照できる案件配下のリスクだけを返す。"""

    return Risk.objects.filter(
        project__in=projects_for(request.user, request.tenant)
    ).select_related("project")


def _issues_for(request: HttpRequest) -> QuerySet[Issue]:
    """参照できる案件配下の課題だけを返す。"""

    return Issue.objects.filter(
        project__in=projects_for(request.user, request.tenant)
    ).select_related("project")


@login_required
def risk_create(request: HttpRequest) -> HttpResponse:
    projects = _editable_projects(request)
    return_to = _return_to(request, RISK_LIST_URL)

    if request.method == "POST":
        form = RiskForm(request.POST, projects=projects)

        if form.is_valid():
            risk = save_risk(form)
            messages.success(request, f"リスク「{risk.title}」を登録しました。")

            return redirect(return_to)

        messages.error(request, "入力内容を確認してください。")
    else:
        form = RiskForm(projects=projects)

    return render(
        request,
        "pages/risk_form.html",
        {
            "form": form,
            "risk": None,
            "page_title": "リスクを新規作成",
            "form_title": "リスク情報",
            "form_subtitle": "影響度と発生確率は 1〜5 で入力します。",
            "return_to": return_to,
        },
    )


@login_required
def risk_edit(request: HttpRequest, pk) -> HttpResponse:
    risk = get_object_or_404(_risks_for(request), pk=pk)
    _require_edit(request, risk.project)
    projects = _editable_projects(request)
    return_to = _return_to(request, RISK_LIST_URL)

    if request.method == "POST":
        form = RiskForm(request.POST, instance=risk, projects=projects)

        if form.is_valid():
            save_risk(form)
            messages.success(request, f"リスク「{risk.title}」を更新しました。")

            return redirect(return_to)

        messages.error(request, "入力内容を確認してください。")
    else:
        form = RiskForm(instance=risk, projects=projects)

    return render(
        request,
        "pages/risk_form.html",
        {
            "form": form,
            "risk": risk,
            "page_title": "リスクを編集",
            "form_title": "リスク情報",
            "form_subtitle": "影響度と発生確率は 1〜5 で入力します。",
            "return_to": return_to,
        },
    )


@login_required
@require_POST
def risk_close(request: HttpRequest, pk) -> HttpResponse:
    risk = get_object_or_404(_risks_for(request), pk=pk)
    _require_edit(request, risk.project)
    close_risk(risk)
    messages.success(request, f"リスク「{risk.title}」をクローズしました。")

    return redirect(_return_to(request, RISK_LIST_URL))


@login_required
def risk_promote(request: HttpRequest, pk) -> HttpResponse:
    """リスクを課題へ転換する。課題作成とリスクの状態遷移はサービス層で同時に確定する。"""

    risk = get_object_or_404(_risks_for(request), pk=pk)
    _require_edit(request, risk.project)
    return_to = _return_to(request, RISK_LIST_URL)

    if request.method == "POST":
        form = RiskPromoteForm(request.POST)

        if form.is_valid():
            issue = promote_risk_to_issue(risk, issue_form=form)
            messages.success(
                request,
                f"リスク「{risk.title}」を顕在化として課題「{issue.title}」に転換しました。",
            )

            return redirect(return_to)

        messages.error(request, "入力内容を確認してください。")
    else:
        form = RiskPromoteForm(
            initial={
                "title": risk.title,
                "description": risk.description,
                "owner": risk.owner,
                "due_date": risk.due_date,
            }
        )

    return render(
        request,
        "pages/issue_form.html",
        {
            "form": form,
            "issue": None,
            "risk": risk,
            "page_title": "リスクを課題へ転換",
            "form_title": "起票する課題",
            "form_subtitle": "顕在化したリスクを課題台帳へ移します。",
            "submit_label": "課題として起票する",
            "return_to": return_to,
        },
    )


@login_required
def issue_list(request: HttpRequest) -> HttpResponse:
    """課題一覧。件数が増えても 1 画面へ詰め込まない。

    UXP-07: 状態・重大度・期限を GET で絞り込む。条件はすべて GET なので
    URL をそのまま共有でき、「条件をクリア」で必ず全件へ戻れる。
    """

    today = timezone.localdate()
    status_choices = Issue.Status.choices
    severity_choices = Severity.choices

    status = _selected_value(request, "status", status_choices)
    severity = _selected_value(request, "severity", severity_choices)
    due = _selected_value(request, "due", ISSUE_DUE_CHOICES)

    base = _scoped(request, _issues_for(request))
    queryset = base
    applied: list[str] = []

    if status:
        queryset = queryset.filter(status=status)
        applied.append(f"状態: {_choice_label(status, status_choices)}")

    if severity:
        queryset = queryset.filter(severity=severity)
        applied.append(f"重大度: {_choice_label(severity, severity_choices)}")

    if due == "overdue":
        queryset = queryset.filter(due_date__lt=today, status__in=ISSUE_OPEN_STATUSES)
    elif due == "soon":
        queryset = queryset.filter(
            due_date__range=(today, today + timedelta(days=7)),
            status__in=ISSUE_OPEN_STATUSES,
        )
    elif due == "none":
        queryset = queryset.filter(due_date__isnull=True)

    if due:
        applied.append(f"期限: {_choice_label(due, ISSUE_DUE_CHOICES)}")

    # 行内で「期限超過」を出し分けるため、判定を DB 側で付ける。
    # テンプレートで日付比較はできないので、ここで確定させる。
    queryset = queryset.annotate(
        is_overdue=ExpressionWrapper(
            Q(due_date__lt=today) & Q(status__in=ISSUE_OPEN_STATUSES),
            output_field=BooleanField(),
        )
    )

    page = paginate(queryset, request)

    return render(
        request,
        "pages/issue_list.html",
        {
            "issues": page.object_list,
            # 外部原文リンクは 1 行ずつ引くと N+1 になる。表示する行だけ先に引く。
            "external_links": synced_records_for(page.object_list),
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "page_title": "課題管理",
            "status_choices": status_choices,
            "severity_choices": severity_choices,
            "due_choices": ISSUE_DUE_CHOICES,
            "filters": {
                "status": status,
                "severity": severity,
                "due": due,
                "applied": applied,
            },
            "is_filtered": bool(applied),
            "has_any": page.paginator.count > 0 or base.exists(),
            "total_count": page.paginator.count,
            "ordering_label": "登録が新しい順",
        },
    )


@login_required
def issue_create(request: HttpRequest) -> HttpResponse:
    projects = _editable_projects(request)
    return_to = _return_to(request, ISSUE_LIST_URL)

    if request.method == "POST":
        form = IssueForm(request.POST, projects=projects)

        if form.is_valid():
            issue = save_issue(form)
            messages.success(request, f"課題「{issue.title}」を登録しました。")

            return redirect(return_to)

        messages.error(request, "入力内容を確認してください。")
    else:
        form = IssueForm(projects=projects)

    return render(
        request,
        "pages/issue_form.html",
        {
            "form": form,
            "issue": None,
            "risk": None,
            "page_title": "課題を新規作成",
            "form_title": "課題情報",
            "form_subtitle": "対応期限と担当を決めてから起票します。",
            "return_to": return_to,
        },
    )


@login_required
def issue_edit(request: HttpRequest, pk) -> HttpResponse:
    issue = get_object_or_404(_issues_for(request), pk=pk)
    _require_edit(request, issue.project)
    projects = _editable_projects(request)
    return_to = _return_to(request, ISSUE_LIST_URL)

    if request.method == "POST":
        form = IssueForm(request.POST, instance=issue, projects=projects)

        if form.is_valid():
            save_issue(form)
            messages.success(request, f"課題「{issue.title}」を更新しました。")

            return redirect(return_to)

        messages.error(request, "入力内容を確認してください。")
    else:
        form = IssueForm(instance=issue, projects=projects)

    return render(
        request,
        "pages/issue_form.html",
        {
            "form": form,
            "issue": issue,
            "risk": None,
            "page_title": "課題を編集",
            "form_title": "課題情報",
            "form_subtitle": "対応期限と担当を決めてから起票します。",
            "return_to": return_to,
        },
    )


@login_required
@require_POST
def issue_close(request: HttpRequest, pk) -> HttpResponse:
    issue = get_object_or_404(_issues_for(request), pk=pk)
    _require_edit(request, issue.project)
    close_issue(issue)
    messages.success(request, f"課題「{issue.title}」をクローズしました。")

    return redirect(_return_to(request, ISSUE_LIST_URL))
