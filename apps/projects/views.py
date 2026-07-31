"""案件一覧・詳細と、WBS タスクの登録・編集・アーカイブ。

タスクの取得は必ず `projects_for` で絞った案件配下に限定する。テナント越境は
「見えない」ではなく「存在しない（404）」として扱い、ID の総当たりで他テナントの
存在有無が漏れないようにする。
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.pagination import page_window, paginate, query_without_page
from apps.projects.forms import (
    ChangeDecisionForm,
    ChangeRequestForm,
    DefectForm,
    IssueForm,
    RiskForm,
    RiskPromoteForm,
    WbsTaskForm,
)
from apps.projects.models import ChangeRequest, Defect, Issue, Project, Risk, WbsTask
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


@login_required
def project_list(request: HttpRequest) -> HttpResponse:
    """案件一覧。総件数はページャに出し、表示だけをページで切る。"""

    # 案件一覧だけは選択中の案件で絞らない。ここは切替の起点なので、
    # 絞った状態だと他の案件へ移れなくなる。
    page = paginate(projects_for(request.user, request.tenant), request)

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
            "page_title": "案件管理",
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
            "page_title": project.name,
        },
    )


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

    if request.method == "POST":
        form = ChangeRequestForm(request.POST, instance=change, projects=projects)

        if form.is_valid():
            saved = save_change_request(form, user=request.user)
            messages.success(request, f"変更要求「{saved.title}」を保存しました。")

            return redirect("dashboard:change")

        messages.error(request, "変更要求を保存できませんでした。入力内容を確認してください。")
    else:
        form = ChangeRequestForm(instance=change, projects=projects)

    title = "変更要求の編集" if change else "変更要求の新規作成"

    return render(
        request,
        "pages/change_form.html",
        {"form": form, "change": change, "form_title": title, "page_title": title},
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

                return redirect("dashboard:change")
        else:
            messages.error(request, "判断を記録できませんでした。入力内容を確認してください。")
    else:
        form = ChangeDecisionForm()

    return render(
        request,
        "pages/change_decide_form.html",
        {"form": form, "change": change, "page_title": "変更要求の判断"},
    )


@login_required
def defect_list(request: HttpRequest) -> HttpResponse:
    """不具合一覧。件数が増えても 1 画面へ詰め込まない。"""

    page = paginate(_scoped(request, _defects_for(request)), request)

    return render(
        request,
        "pages/defect_list.html",
        {
            "defects": page.object_list,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "page_title": "不具合管理",
        },
    )


def _render_defect_form(request: HttpRequest, *, defect: Defect | None) -> HttpResponse:
    if defect is not None:
        _require_edit(request, defect.project)

    projects = _editable_projects(request)

    if request.method == "POST":
        form = DefectForm(request.POST, instance=defect, projects=projects)

        if form.is_valid():
            saved = save_defect(form, user=request.user)
            messages.success(request, f"不具合「{saved.title}」を保存しました。")

            return redirect("projects:defect_list")

        messages.error(request, "不具合を保存できませんでした。入力内容を確認してください。")
    else:
        form = DefectForm(instance=defect, projects=projects)

    title = "不具合の編集" if defect else "不具合の新規登録"

    return render(
        request,
        "pages/defect_form.html",
        {"form": form, "defect": defect, "form_title": title, "page_title": title},
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

    return redirect("projects:defect_list")


def _tasks_for(request: HttpRequest) -> QuerySet[WbsTask]:
    """参照できる案件配下のタスクだけを返す。"""

    return WbsTask.objects.filter(
        project__in=projects_for(request.user, request.tenant)
    ).select_related("project")


@login_required
def task_create(request: HttpRequest) -> HttpResponse:
    projects = _editable_projects(request)

    if request.method == "POST":
        form = WbsTaskForm(request.POST, projects=projects)

        if form.is_valid():
            task = create_task(form)
            messages.success(request, f"タスク「{task.name}」を作成しました。")

            return redirect(TASK_LIST_URL)

        messages.error(request, "入力内容を確認してください。")
    else:
        form = WbsTaskForm(projects=projects)

    return render(
        request,
        "pages/task_form.html",
        {"form": form, "task": None, "page_title": "タスクを新規作成"},
    )


@login_required
def task_edit(request: HttpRequest, pk) -> HttpResponse:
    task = get_object_or_404(_tasks_for(request), pk=pk)
    _require_edit(request, task.project)
    projects = _editable_projects(request)

    if request.method == "POST":
        form = WbsTaskForm(request.POST, instance=task, projects=projects)

        if form.is_valid():
            update_task(form)
            messages.success(request, f"タスク「{task.name}」を更新しました。")

            return redirect(TASK_LIST_URL)

        messages.error(request, "入力内容を確認してください。")
    else:
        form = WbsTaskForm(instance=task, projects=projects)

    return render(
        request,
        "pages/task_form.html",
        {"form": form, "task": task, "page_title": "タスクを編集"},
    )


@login_required
def task_detail(request: HttpRequest, pk) -> HttpResponse:
    task = get_object_or_404(_tasks_for(request), pk=pk)

    return render(
        request,
        "pages/task_detail.html",
        {
            "task": task,
            "related_tasks": task.related_tasks.all(),
            "child_tasks": task.children.all(),
            "page_title": f"{task.wbs_code} {task.name}",
        },
    )


@login_required
@require_POST
def task_archive(request: HttpRequest, pk) -> HttpResponse:
    task = get_object_or_404(_tasks_for(request), pk=pk)
    _require_edit(request, task.project)
    archive_task(task)
    messages.success(request, f"タスク「{task.name}」をアーカイブしました。")

    return redirect(TASK_LIST_URL)


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

    if request.method == "POST":
        form = RiskForm(request.POST, projects=projects)

        if form.is_valid():
            risk = save_risk(form)
            messages.success(request, f"リスク「{risk.title}」を登録しました。")

            return redirect(RISK_LIST_URL)

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
        },
    )


@login_required
def risk_edit(request: HttpRequest, pk) -> HttpResponse:
    risk = get_object_or_404(_risks_for(request), pk=pk)
    _require_edit(request, risk.project)
    projects = _editable_projects(request)

    if request.method == "POST":
        form = RiskForm(request.POST, instance=risk, projects=projects)

        if form.is_valid():
            save_risk(form)
            messages.success(request, f"リスク「{risk.title}」を更新しました。")

            return redirect(RISK_LIST_URL)

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
        },
    )


@login_required
@require_POST
def risk_close(request: HttpRequest, pk) -> HttpResponse:
    risk = get_object_or_404(_risks_for(request), pk=pk)
    _require_edit(request, risk.project)
    close_risk(risk)
    messages.success(request, f"リスク「{risk.title}」をクローズしました。")

    return redirect(RISK_LIST_URL)


@login_required
def risk_promote(request: HttpRequest, pk) -> HttpResponse:
    """リスクを課題へ転換する。課題作成とリスクの状態遷移はサービス層で同時に確定する。"""

    risk = get_object_or_404(_risks_for(request), pk=pk)
    _require_edit(request, risk.project)

    if request.method == "POST":
        form = RiskPromoteForm(request.POST)

        if form.is_valid():
            issue = promote_risk_to_issue(risk, issue_form=form)
            messages.success(
                request,
                f"リスク「{risk.title}」を顕在化として課題「{issue.title}」に転換しました。",
            )

            return redirect(ISSUE_LIST_URL)

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
        },
    )


@login_required
def issue_list(request: HttpRequest) -> HttpResponse:
    """課題一覧。件数が増えても 1 画面へ詰め込まない。"""

    page = paginate(_scoped(request, _issues_for(request)), request)

    return render(
        request,
        "pages/issue_list.html",
        {
            "issues": page.object_list,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "page_title": "課題管理",
        },
    )


@login_required
def issue_create(request: HttpRequest) -> HttpResponse:
    projects = _editable_projects(request)

    if request.method == "POST":
        form = IssueForm(request.POST, projects=projects)

        if form.is_valid():
            issue = save_issue(form)
            messages.success(request, f"課題「{issue.title}」を登録しました。")

            return redirect(ISSUE_LIST_URL)

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
        },
    )


@login_required
def issue_edit(request: HttpRequest, pk) -> HttpResponse:
    issue = get_object_or_404(_issues_for(request), pk=pk)
    _require_edit(request, issue.project)
    projects = _editable_projects(request)

    if request.method == "POST":
        form = IssueForm(request.POST, instance=issue, projects=projects)

        if form.is_valid():
            save_issue(form)
            messages.success(request, f"課題「{issue.title}」を更新しました。")

            return redirect(ISSUE_LIST_URL)

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
        },
    )


@login_required
@require_POST
def issue_close(request: HttpRequest, pk) -> HttpResponse:
    issue = get_object_or_404(_issues_for(request), pk=pk)
    _require_edit(request, issue.project)
    close_issue(issue)
    messages.success(request, f"課題「{issue.title}」をクローズしました。")

    return redirect(ISSUE_LIST_URL)
