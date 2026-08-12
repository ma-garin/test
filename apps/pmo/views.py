"""PMO 支援画面。"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.constants import APPROVER_ROLES
from apps.agents.models import AgentRun
from apps.agents.services import orchestrator
from apps.core import screen_context
from apps.core.pagination import page_window, paginate, query_without_page
from apps.documents.selectors import templates_for
from apps.documents.services import template_export
from apps.pmo import selectors
from apps.pmo.forms import DeliverableEditForm, DeliverableGenerateForm
from apps.pmo.models import Deliverable, PlanDraft
from apps.pmo.services import approval as approval_service
from apps.pmo.services import deliverables as deliverable_service
from apps.pmo.services import diffing, fact_check, generators
from apps.pmo.services import prompt_library as prompt_library_service
from apps.projects.selectors import scoped_projects_for
from apps.rag.models import VectorIndex

#: 相談文の手本（UXP-14）。入力前に「何を書けば答えが返るのか」を示すために出す。
CONSULTATION_EXAMPLES = (
    "結合試験が5日遅れています。挽回策を判断するために何を確認すべきですか。",
    "未解決の課題が20件あります。どれから着手すべきか優先度を整理してください。",
    "顧客報告の前に、品質指標のどこを説明できるようにしておくべきですか。",
)

#: 相談画面の「対象案件の確認」に並べる件数。全件並べても読まれないため頭だけ出す。
TARGET_PROJECT_PREVIEW = 5

#: レビュー観点の確認済み判定の根拠。画面にも出して、利用者が結果を検算できるようにする。
REVIEW_BASIS_NOTE = (
    "確認済みの判定: 計画本文にその観点が書かれているか"
    "（状態が「確定」の計画は全件確認済み）。チェック状態は保存していません。"
)

#: 成果物支援の固定手順（UXP-16）。並びは選択状態によらず常に同じにする。
DELIVERABLE_STEPS = (
    ("generate", "生成", "案件と種類を選び、［生成する］を押します。"),
    ("compare", "比較", "AI生成本文と実データを見比べ、直す箇所を決めます。"),
    ("finalize", "確定", "確定本文を編集し、［確定本文を保存］を押します。"),
    ("approve", "承認申請", "［承認へ進む］から承認・承認依頼を出します。"),
)

#: 「確定本文を保存」を主操作として出す手順。
EDIT_STEPS = ("compare", "finalize")

#: 承認権限が無い利用者へ出す説明（UXP-17）。押せない理由をロールの言葉で伝える。
PERMISSION_NOTE = (
    "承認・承認依頼を出せるのは PMO担当・PM/PL・品質責任者・"
    "テナント管理者・システム管理者のロールだけです。"
)


@dataclass(frozen=True)
class ReviewItem:
    """レビュー観点 1 件の表示単位。永続化しないので毎回導出する。"""

    text: str
    confirmed: bool


@dataclass(frozen=True)
class StepRow:
    """成果物支援の手順 1 つ。"""

    index: int
    key: str
    label: str
    action: str
    state: str
    is_current: bool
    is_done: bool

    @property
    def is_edit_step(self) -> bool:
        return self.key in EDIT_STEPS


@dataclass(frozen=True)
class BlockedRow:
    """承認できない成果物 1 件と、その理由の区分。"""

    row: object
    category: str
    reason: str
    next_action: str


def _review_items(draft: PlanDraft | None) -> list[ReviewItem]:
    """レビュー観点へ「確認済み／未確認」を付ける（UXP-15）。

    チェック状態は保存しない。確定済みの計画は全件確認済み、それ以外は計画本文に
    その観点が書かれているものだけを確認済みとみなす。判定根拠は画面にも出す。
    """

    if draft is None:
        return []

    finalized = draft.status == PlanDraft.Status.FINALIZED
    body = draft.body or ""

    return [
        ReviewItem(text=str(point), confirmed=finalized or str(point) in body)
        for point in draft.review_points
    ]


def _current_step_key(selected) -> str:
    """成果物支援でいまいる手順（UXP-16）。"""

    if selected is None:
        return "generate"

    deliverable = selected.deliverable

    if deliverable.status in (Deliverable.Status.PENDING_APPROVAL, Deliverable.Status.APPROVED):
        return "approve"

    if not deliverable.body or deliverable.body == deliverable.ai_generated_body:
        return "compare"

    return "finalize"


def _step_rows(current_key: str) -> list[StepRow]:
    """固定手順を、現在位置つきの表示単位へ畳む。"""

    position = [key for key, _, _ in DELIVERABLE_STEPS].index(current_key)

    return [
        StepRow(
            index=index + 1,
            key=key,
            label=label,
            action=action,
            state="済" if index < position else ("実行中" if index == position else "未着手"),
            is_current=index == position,
            is_done=index < position,
        )
        for index, (key, label, action) in enumerate(DELIVERABLE_STEPS)
    ]


def _blocked_rows(rows, *, can_decide: bool) -> list[BlockedRow]:
    """承認できない行を理由の区分ごとに畳む（UXP-17）。

    根拠不足と権限不足は直し方が違うので、同じ「できない」でも区分を分けて返す。
    """

    blocked: list[BlockedRow] = []

    for row in rows:
        if not row.can_approve:
            blocked.append(
                BlockedRow(
                    row=row,
                    category="根拠不足",
                    reason=row.blocking_reason,
                    next_action="根拠を追加し、根拠評価を通してから申請する",
                )
            )
        elif not can_decide:
            blocked.append(
                BlockedRow(
                    row=row,
                    category="権限",
                    reason=PERMISSION_NOTE,
                    next_action="承認権限のある担当者へ依頼する",
                )
            )

    return blocked


@login_required
def consultation(request: HttpRequest) -> HttpResponse:
    """PMO 相談。オーケストレーターを通し、意図・計画・根拠評価を画面へ返す。"""

    question = request.GET.get("q", "").strip()
    # 未入力のまま送信されたことを、まだ押していない状態と区別する。
    # 同じ画面を返すだけだと「押しても何も起きない」不具合に見える。
    submitted_empty = "q" in request.GET and not question
    # 直前に開いていた画面を文脈として使う（要件 #22）。使うかどうかは
    # 利用者が選べる。勝手に混ぜて外した検索をされるほうが困るため。
    screen = screen_context.current(request)
    # チェックを外すと `use_screen` は送られてこない。フォームを出したこと自体を
    # `screen_form` で示し、「外した」と「まだ出していない」を区別する。
    use_screen = (
        request.GET.get("use_screen") == "1"
        if request.GET.get("screen_form")
        else True
    )
    result = None

    if question and request.tenant:
        index = VectorIndex.objects.filter(tenant=request.tenant, project__isnull=True).first()
        result = orchestrator.run(
            tenant=request.tenant,
            question=question,
            area=AgentRun.Area.PMO_CONSULTATION,
            index=index,
            user=request.user,
            screen_hint=screen.as_hint if screen and use_screen else "",
        )

    # 入力前に「何を書けば答えが返るか」と「どの案件が対象か」を示す（UXP-14）。
    projects = scoped_projects_for(request)

    return render(
        request,
        "pages/pmo_consultation.html",
        {
            "question": question,
            "submitted_empty": submitted_empty,
            "result": result,
            "screen": screen,
            "use_screen": use_screen,
            "examples": CONSULTATION_EXAMPLES,
            "target_projects": projects[:TARGET_PROJECT_PREVIEW],
            "target_project_count": projects.count(),
            "page_title": "PMO相談・状況整理",
        },
    )


@login_required
def planning(request: HttpRequest) -> HttpResponse:
    """計画ドラフト。ドラフト一覧と、選択した 1 件のレビュー観点を出す。"""

    drafts = selectors.plan_drafts_for(request.user, request.tenant)
    page = paginate(drafts, request)
    # 選択中の 1 件は全件から解決する。2 ページ目の行を開いたときに
    # 詳細だけ先頭の計画へ化ける、という壊れ方を防ぐため。
    all_drafts = list(drafts)
    selected = _pick(all_drafts, request.GET.get("draft"))
    review_items = _review_items(selected)
    unconfirmed_count = sum(1 for item in review_items if not item.confirmed)

    return render(
        request,
        "pages/pmo_planning.html",
        {
            "drafts": page.object_list,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "selected": selected,
            "review_items": review_items,
            "unconfirmed_count": unconfirmed_count,
            # 観点が 1 件も無い計画を「確定してよい」と言い切らない。
            "can_finalize": bool(review_items) and unconfirmed_count == 0,
            "review_basis": REVIEW_BASIS_NOTE,
            "page_title": "計画ドラフト",
        },
    )


@login_required
def deliverables(request: HttpRequest) -> HttpResponse:
    """成果物支援。生成・編集・差分確認をこの 1 画面で完結させる。

    生成（POST action=generate）と確定本文の保存（POST action=save）を同じ URL で
    受ける。成果物の一覧・差分・編集フォームは同じ選択状態を共有するため、
    別画面へ分けると「どれを直しているのか」が分からなくなる。
    """

    if request.method == "POST":
        return _deliverable_post(request)

    return render(request, "pages/pmo_deliverables.html", _deliverables_context(request))


def _deliverables_context(
    request: HttpRequest,
    *,
    selected_pk: str | None = None,
    generate_form=None,
    edit_form=None,
) -> dict:
    """成果物画面の描画データ。POST 失敗時の再描画からも同じものを使う。"""

    # 集計（件数・平均赤字率）は全件から出す。ページを送るたびに KPI が動くと、
    # その数字が現状を表していないことになるため。
    report = deliverable_service.build_report(
        selectors.deliverables_for(request.user, request.tenant)
    )
    page = paginate(report.rows, request)
    selected = _pick(report.rows, selected_pk or request.GET.get("deliverable"), key=_row_pk)
    projects = scoped_projects_for(request)

    if selected is not None and edit_form is None:
        edit_form = DeliverableEditForm(instance=selected.deliverable)

    # 手順は選択状態によらず常に 4 段すべて出す（UXP-16）。主操作は現在段だけに絞る。
    steps = _step_rows(_current_step_key(selected))

    return {
        "report": report,
        "steps": steps,
        "current_step": next(step for step in steps if step.is_current),
        "page": page,
        "page_window": page_window(page),
        "page_query": query_without_page(request),
        "selected": selected,
        "target_percent": deliverable_service.CORRECTION_RATE_TARGET_PERCENT,
        "generate_form": generate_form or DeliverableGenerateForm(projects=projects),
        "edit_form": edit_form,
        "diff": diffing.line_diff(
            selected.deliverable.ai_generated_body, selected.deliverable.body
        )
        if selected
        else None,
        "can_edit": bool(selected)
        and selected.deliverable.status != Deliverable.Status.APPROVED,
        "generators": generators.GENERATORS,
        # ひな型が 1 件も無いときは出力欄ごと出さない。押しても何も起きない
        # ボタンを置くと、機能が壊れているのか未設定なのか区別できない。
        "export_templates": templates_for(request.user, request.tenant),
        # 本文中の数値を実データと突き合わせる（要件 #15）。人が直した後こそ
        # 検証したいので、確定本文を保存するたびに出し直す。
        "fact_check": fact_check.check(selected.deliverable) if selected else None,
        "fact_check_note": fact_check.UNCHECKABLE_NOTE,
        "page_title": "成果物支援",
    }


def _deliverable_post(request: HttpRequest) -> HttpResponse:
    """成果物画面の POST。生成と保存のどちらかを受ける。"""

    action = request.POST.get("action", "")

    if action == "generate":
        return _generate_deliverable(request)

    if action == "save":
        return _save_deliverable(request)

    if action == "export":
        return _export_deliverable(request)

    messages.error(request, "不明な操作です。")

    return redirect("pmo:deliverables")


def _generate_deliverable(request: HttpRequest) -> HttpResponse:
    """実データから成果物を生成する。案件は参照できる範囲からしか選べない。"""

    projects = scoped_projects_for(request)
    form = DeliverableGenerateForm(request.POST, projects=projects)

    if not form.is_valid():
        messages.error(request, "成果物を生成できませんでした。入力内容を確認してください。")

        return render(
            request,
            "pages/pmo_deliverables.html",
            _deliverables_context(request, generate_form=form),
        )

    result = generators.generate_and_save(
        project=form.cleaned_data["project"],
        generator_key=form.cleaned_data["generator"],
        user=request.user,
        notes=form.cleaned_data.get("notes", ""),
    )

    if not result.ok:
        messages.error(request, result.message)

        return redirect("pmo:deliverables")

    if result.document is not None and not result.document.has_material:
        messages.warning(request, result.message)
    else:
        messages.success(request, result.message)

    return redirect(f"{reverse('pmo:deliverables')}?deliverable={result.deliverable.pk}")


def _export_deliverable(request: HttpRequest) -> HttpResponse:
    """成果物を Excel ひな型へ書き出す（要件 #62）。

    書けなかった項目があっても出力自体は成功させる。ここで失敗にすると、
    「1 項目のマッピング漏れで報告書が 1 枚も出せない」ことになるため。
    ただし書けなかった理由は必ず画面へ出す。
    """

    deliverable = get_object_or_404(
        selectors.deliverables_for(request.user, request.tenant),
        pk=request.POST.get("deliverable"),
    )
    # ひな型も参照可能な範囲からしか選ばせない。他テナントのひな型は解決しない。
    template = get_object_or_404(
        templates_for(request.user, request.tenant), pk=request.POST.get("template")
    )
    result = template_export.export(template, deliverable, user=request.user)

    if not result.ok:
        for error in result.errors:
            messages.error(request, error)
    else:
        messages.success(request, f"{result.written_count}項目をひな型へ書き出しました。")

        for warning in result.warnings:
            messages.warning(request, warning)

    return redirect(f"{reverse('pmo:deliverables')}?deliverable={deliverable.pk}")


def _save_deliverable(request: HttpRequest) -> HttpResponse:
    """確定本文を保存する。承認済みの版は書き換えさせない。"""

    deliverable = get_object_or_404(
        selectors.deliverables_for(request.user, request.tenant),
        pk=request.POST.get("deliverable"),
    )

    if deliverable.status == Deliverable.Status.APPROVED:
        messages.error(request, "承認済みの版は編集できません。新しい版を生成してください。")

        return redirect(f"{reverse('pmo:deliverables')}?deliverable={deliverable.pk}")

    form = DeliverableEditForm(request.POST, instance=deliverable)

    if not form.is_valid():
        messages.error(request, "確定本文を保存できませんでした。入力内容を確認してください。")

        return render(
            request,
            "pages/pmo_deliverables.html",
            _deliverables_context(request, selected_pk=str(deliverable.pk), edit_form=form),
        )

    saved = form.save()
    rate = saved.correction_rate
    detail = "（AI未使用のため赤字率は算出しません）" if rate is None else f"（赤字率 {round(rate * 100)}%）"
    messages.success(request, f"確定本文を保存しました。{detail}")

    return redirect(f"{reverse('pmo:deliverables')}?deliverable={saved.pk}")


@login_required
def approvals(request: HttpRequest) -> HttpResponse:
    """報告生成・承認。承認は POST で受け、判断とその履歴を残す。"""

    if request.method == "POST":
        return _decide(request)

    # 判断待ち件数・ブロック件数は全件から数える。ページ送りで警告の件数が
    # 減って見えると、対応漏れの原因になる。
    report = deliverable_service.build_report(
        selectors.deliverables_awaiting_decision_for(request.user, request.tenant)
    )
    page = paginate(report.rows, request)
    rows = list(page.object_list)
    # 承認できるロールかどうかで、行を「操作できる」「できない」に振り分ける（UXP-17）。
    can_decide = request.user.role in APPROVER_ROLES

    return render(
        request,
        "pages/pmo_approvals.html",
        {
            "report": report,
            "page": page,
            "can_decide": can_decide,
            "permission_note": PERMISSION_NOTE,
            "actionable_rows": [row for row in rows if row.can_approve] if can_decide else [],
            "blocked_rows": _blocked_rows(rows, can_decide=can_decide),
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "history": selectors.approvals_for(request.user, request.tenant)[:20],
            "page_title": "報告生成・承認",
        },
    )


def _decide(request: HttpRequest) -> HttpResponseRedirect:
    """承認画面の POST。テナント外の成果物は 404 にして触らせない。"""

    deliverable = get_object_or_404(
        selectors.deliverables_for(request.user, request.tenant),
        pk=request.POST.get("deliverable"),
    )
    result = approval_service.decide(
        deliverable=deliverable,
        actor=request.user,
        decision=request.POST.get("decision", ""),
        comment=request.POST.get("comment", "").strip(),
    )

    if result.ok:
        messages.success(request, result.message)
    else:
        messages.error(request, result.message)

    return redirect("pmo:approvals")


@login_required
def prompt_library(request: HttpRequest) -> HttpResponse:
    """プロンプトライブラリ。相談画面へ本文を渡すリンクを持つ。"""

    entries = prompt_library_service.entries_for(request.tenant)

    return render(
        request,
        "pages/pmo_prompt_library.html",
        {
            "entries": entries,
            "categories": prompt_library_service.categories(entries),
            "page_title": "プロンプトライブラリ",
        },
    )


@login_required
def education(request: HttpRequest) -> HttpResponse:
    """教育支援。新任 PMO 向けの操作導線と用語解説。"""

    return render(
        request,
        "pages/pmo_education.html",
        {"deliverable_kinds": Deliverable.Kind.choices, "page_title": "教育支援"},
    )


def _row_pk(row):
    return row.deliverable.pk


def _pick(items: list, raw_pk: str | None, key=lambda item: item.pk):
    """一覧から選択中の 1 件を返す。指定が無ければ先頭。

    pk は UUID なので数値として解釈せず、文字列のまま突き合わせる。
    URL に不正な pk が来ても画面を落とさないため、例外にせず先頭へ倒す。
    """

    if not items:
        return None

    if raw_pk:
        return next((item for item in items if str(key(item)) == raw_pk), items[0])

    return items[0]
