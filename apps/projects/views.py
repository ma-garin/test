"""案件一覧・詳細と、WBS タスクの登録・編集・アーカイブ。

タスクの取得は必ず `projects_for` で絞った案件配下に限定する。テナント越境は
「見えない」ではなく「存在しない（404）」として扱い、ID の総当たりで他テナントの
存在有無が漏れないようにする。
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.projects.forms import IssueForm, RiskForm, RiskPromoteForm, WbsTaskForm
from apps.projects.models import Issue, Risk, WbsTask
from apps.projects.forms import ChangeDecisionForm, ChangeRequestForm, DefectForm
from apps.projects.models import ChangeRequest, Defect
from apps.projects.selectors import projects_for
from apps.projects.services.change_requests import decide_change_request, save_change_request
from apps.projects.services.defects import close_defect, save_defect
from apps.projects.services.issues import close_issue, save_issue
from apps.projects.services.risks import close_risk, promote_risk_to_issue, save_risk
from apps.projects.services.tasks import archive_task, create_task, update_task

TASK_LIST_URL = "dashboard:tasks"
RISK_LIST_URL = "dashboard:risk"
ISSUE_LIST_URL = "projects:issue_list"


@login_required
def project_list(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "pages/project_list.html",
        {"projects": projects_for(request.user, request.tenant), "page_title": "案件管理"},
    )


@login_required
def project_detail(request: HttpRequest, pk) -> HttpResponse:
    project = get_object_or_404(projects_for(request.user, request.tenant), pk=pk)

    return render(
        request,
        "pages/project_detail.html",
        {"project": project, "page_title": project.name},
    )


# --- 変更要求・不具合 -------------------------------------------------------
# 取得は必ず「参照できる案件」に紐づくものへ絞る。絞り込みを個々のビューで
# 書くと必ずどこかで漏れるため、下の 2 つのヘルパー以外から直接 objects を
# 触らないこと。


def _change_requests_for(request: HttpRequest):
    return ChangeRequest.objects.filter(
        project__in=projects_for(request.user, request.tenant)
    ).select_related("project")


def _defects_for(request: HttpRequest):
    return Defect.objects.filter(
        project__in=projects_for(request.user, request.tenant)
    ).select_related("project")


def _render_change_form(request: HttpRequest, *, change: ChangeRequest | None) -> HttpResponse:
    projects = projects_for(request.user, request.tenant)

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

    if not request.user.can_approve:
        raise PermissionDenied("変更要求を判断する権限がありません。")

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
    return render(
        request,
        "pages/defect_list.html",
        {"defects": _defects_for(request), "page_title": "不具合管理"},
    )


def _render_defect_form(request: HttpRequest, *, defect: Defect | None) -> HttpResponse:
    projects = projects_for(request.user, request.tenant)

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

    defect = close_defect(get_object_or_404(_defects_for(request), pk=pk), user=request.user)
    messages.success(request, f"不具合「{defect.title}」をクローズしました。")

    return redirect("projects:defect_list")


def _tasks_for(request: HttpRequest) -> QuerySet[WbsTask]:
    """参照できる案件配下のタスクだけを返す。"""

    return WbsTask.objects.filter(
        project__in=projects_for(request.user, request.tenant)
    ).select_related("project")


@login_required
def task_create(request: HttpRequest) -> HttpResponse:
    projects = projects_for(request.user, request.tenant)

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
    projects = projects_for(request.user, request.tenant)

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
    projects = projects_for(request.user, request.tenant)

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
    projects = projects_for(request.user, request.tenant)

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
    close_risk(risk)
    messages.success(request, f"リスク「{risk.title}」をクローズしました。")

    return redirect(RISK_LIST_URL)


@login_required
def risk_promote(request: HttpRequest, pk) -> HttpResponse:
    """リスクを課題へ転換する。課題作成とリスクの状態遷移はサービス層で同時に確定する。"""

    risk = get_object_or_404(_risks_for(request), pk=pk)

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
    return render(
        request,
        "pages/issue_list.html",
        {"issues": _issues_for(request), "page_title": "課題管理"},
    )


@login_required
def issue_create(request: HttpRequest) -> HttpResponse:
    projects = projects_for(request.user, request.tenant)

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
    projects = projects_for(request.user, request.tenant)

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
    close_issue(issue)
    messages.success(request, f"課題「{issue.title}」をクローズしました。")

    return redirect(ISSUE_LIST_URL)
