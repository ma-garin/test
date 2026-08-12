"""承認センターと Work Item 詳細。

ビューは薄く保つ。判定・状態遷移は `services.workflow` / `services.policy`
を呼ぶだけで、ここでは行わない（forbidden_actions: 画面だけの権限判定）。
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.constants import APPROVER_ROLES
from apps.pmo_automation.models import ApprovalStatus, PmoWorkItem, WorkItemState
from apps.pmo_automation.services import workflow
from apps.projects.selectors import projects_for

#: 承認センターに表示する状態。terminal state（completed/cancelled）は出さない。
APPROVAL_CENTER_STATES = (
    WorkItemState.AWAITING_CONFIRMATION,
    WorkItemState.AWAITING_APPROVAL,
    WorkItemState.HOLD,
)

#: 承認パケット7節の並び順（docs/agent/pmo_autopilot_contract.json の
#: approval_packet_sections と一対一）。テンプレート側はこの順で固定表示する。
APPROVAL_PACKET_SECTIONS = (
    ("requested_action", "1. 何を実行するか"),
    ("why_now", "2. なぜ今必要か"),
    ("before_after_diff", "3. 実行前後の差分"),
    ("evidence", "4. 根拠"),
    ("impact_of_not_acting", "5. 実行しない場合の影響"),
    ("automation_level_and_required_role", "6. 自動化レベルと必要な承認ロール"),
    ("decision_and_reason", "7. 承認／差し戻し／却下／保留の選択肢と理由"),
)

#: 承認パケットに出す根拠の上限件数（提案.md 5.4節）。
MAX_EVIDENCE_IN_PACKET = 5


def _work_items_for(user, tenant) -> QuerySet[PmoWorkItem]:
    """参照できる案件の Work Item だけに絞り込む（テナント・案件スコープの唯一の起点）。"""

    return PmoWorkItem.objects.filter(project__in=projects_for(user, tenant))


@login_required
def approval_center(request: HttpRequest) -> HttpResponse:
    """確認待ち・承認待ち・保留を優先順に一覧表示する。"""

    work_items = (
        _work_items_for(request.user, request.tenant)
        .filter(state__in=APPROVAL_CENTER_STATES)
        .select_related("project")
        .order_by("state", "due_at", "-priority")
    )
    can_decide = request.user.role in APPROVER_ROLES

    return render(
        request,
        "pages/pmo_approval_center.html",
        {
            "work_items": work_items,
            "can_decide": can_decide,
            "page_title": "承認センター",
        },
    )


@login_required
def work_item_detail(request: HttpRequest, pk) -> HttpResponse:
    """Work Item 詳細。承認パケット7節を固定順で表示し、決定は POST で受ける。"""

    if request.method == "POST":
        return _decide(request, pk)

    work_item = get_object_or_404(
        _work_items_for(request.user, request.tenant).select_related("project", "tenant"),
        pk=pk,
    )
    plan = work_item.plans.order_by("-version").first()
    steps = plan.steps.all().order_by("order") if plan is not None else []
    evidence_bundles = work_item.evidence_bundles.all().order_by("-captured_at")[:MAX_EVIDENCE_IN_PACKET]
    approval = (
        work_item.approval_requests.filter(status=ApprovalStatus.PENDING).order_by("-created_at").first()
    )
    can_decide = request.user.role in APPROVER_ROLES

    return render(
        request,
        "pages/pmo_work_item_detail.html",
        {
            "work_item": work_item,
            "plan": plan,
            "steps": steps,
            "evidence_bundles": evidence_bundles,
            "approval": approval,
            "approval_history": work_item.approval_requests.exclude(status=ApprovalStatus.PENDING).order_by(
                "-updated_at"
            )[:10],
            "can_decide": can_decide,
            "links": work_item.links.all(),
            "packet_sections": APPROVAL_PACKET_SECTIONS,
            "page_title": "PMO Work Item 詳細",
        },
    )


def _decide(request: HttpRequest, pk) -> HttpResponse:
    """承認パケット7節目（決定）の POST。理由・権限・失効・plan版を必ず再検証する。

    画面表示時の判定を信用しない（FR-02）。ここでの再検証は:
    - reason: 空なら保存せず即エラー（理由なし承認の禁止）。
    - 権限: request.user.role を毎回再確認する（画面のボタン非表示に頼らない）。
      APPROVER_ROLES（汎用の承認可能ロール集合）に加え、この承認個別の
      ApprovalRequest.required_role が設定されていれば、それとも一致するか
      確認する（レビュー指摘: 汎用ロールだけでは、この承認には本来
      不適切なロールでも decided_by として記録されてしまう）。
    - 失効: ApprovalRequest.status が PENDING でなければ
      `workflow.decide_approval` が ValueError で拒否する。
    - plan版: POST に埋め込んだ plan_version が現在の最新版と一致するか確認する。
    """

    work_item = get_object_or_404(_work_items_for(request.user, request.tenant), pk=pk)
    approval = get_object_or_404(
        work_item.approval_requests, pk=request.POST.get("approval")
    )
    decision = request.POST.get("decision", "")
    reason = request.POST.get("reason", "").strip()
    posted_plan_version = request.POST.get("plan_version", "")

    if not reason:
        messages.error(request, "理由を入力してください。理由の無い承認・却下は記録できません。")
        return redirect("pmo_automation:work_item_detail", pk=work_item.pk)

    if request.user.role not in APPROVER_ROLES:
        messages.error(request, "あなたのロールでは承認操作ができません。")
        return redirect("pmo_automation:work_item_detail", pk=work_item.pk)

    if approval.required_role and request.user.role != approval.required_role:
        messages.error(request, "この承認にはあなたのロールでは判断できません（必要ロールが異なります）。")
        return redirect("pmo_automation:work_item_detail", pk=work_item.pk)

    current_plan = work_item.plans.order_by("-version").first()
    if current_plan is not None and posted_plan_version and str(current_plan.version) != posted_plan_version:
        messages.error(request, "計画が更新されています。最新の内容を確認してからやり直してください。")
        return redirect("pmo_automation:work_item_detail", pk=work_item.pk)

    try:
        workflow.decide_approval(
            approval,
            actor_id=request.user.id,
            decision=decision,
            decided_by=request.user,
            decision_reason=reason,
            now=timezone.now(),
        )
    except (ValueError, workflow.SelfApprovalError) as error:
        messages.error(request, str(error))
        return redirect("pmo_automation:work_item_detail", pk=work_item.pk)

    messages.success(request, "判断を記録しました。")
    return redirect("pmo_automation:work_item_detail", pk=work_item.pk)
