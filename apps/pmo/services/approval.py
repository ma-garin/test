"""承認ゲートの業務ロジック。

「根拠が不十分なら承認へ進ませない」がこのシステムの中核なので、その判定と
状態遷移をビューから切り離してここに置く。画面は結果メッセージを出すだけにする。

状態はモデルを直接書き換えず `QuerySet.update()` で更新する。渡された
インスタンスを副作用で変えないほうが、呼び出し側の想定が壊れにくい。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from apps.pmo.models import Approval, Deliverable
from apps.pmo.services import fact_check

#: 判断ごとに「遷移前に許される状態」と「遷移後の状態」。
_TRANSITIONS: dict[str, tuple[tuple[str, ...], str]] = {
    Approval.Decision.REQUESTED: (
        (Deliverable.Status.DRAFT, Deliverable.Status.REJECTED),
        Deliverable.Status.PENDING_APPROVAL,
    ),
    Approval.Decision.APPROVED: (
        (Deliverable.Status.PENDING_APPROVAL,),
        Deliverable.Status.APPROVED,
    ),
    Approval.Decision.REJECTED: (
        (Deliverable.Status.PENDING_APPROVAL,),
        Deliverable.Status.REJECTED,
    ),
}

#: 承認方向（差し戻し以外）は根拠評価のゲートを通す必要がある。
_EVIDENCE_GATED = (Approval.Decision.REQUESTED, Approval.Decision.APPROVED)


@dataclass(frozen=True)
class DecisionResult:
    """状態遷移の結果。画面へ出すメッセージまで含めて返す。"""

    ok: bool
    message: str


def require_separate_approver() -> bool:
    """四眼原則（申請者と承認者を別人にする）を必須にするか。

    既定は必須。ただし承認者が 1 人しかいないテナントでは業務が止まるため、
    `settings.APPROVAL_REQUIRE_SEPARATE_APPROVER = False` で緩められるようにする。
    設定が無い環境でも締まった側で動くよう、既定値は True にする。
    """

    return bool(getattr(settings, "APPROVAL_REQUIRE_SEPARATE_APPROVER", True))


def blocking_reason(
    deliverable: Deliverable,
    *,
    decision: str | None = None,
    actor=None,
    fact_result: fact_check.FactCheckResult | None = None,
    facts_cache: dict | None = None,
) -> str:
    """承認へ進めない理由。進めるなら空文字。

    画面でボタンを無効化するだけでは利用者が理由を判断できないため、
    根拠評価のどの項目で止まっているかを文章で返す。

    根拠評価に加えて、本文の数値・固有名詞が実データと食い違っていないか
    （事実照合）、人が確定本文を書いたか、承認者が申請者と別人か（四眼原則）も
    ここで見る。ゲートを 2 か所に分けると、片方だけを通って承認される抜け道が
    できるため、承認可否の判断はこの関数に集約する。
    `fact_result` は一覧などで既に照合済みの結果を渡し、再計算を避けるため。

    `decision` を省略したときは、いまの状態から次に行う判断を推定する
    （承認待ちなら「承認」、それ以外なら「承認依頼」）。一覧画面は判断を
    指定せずに理由だけを引くため。
    """

    decision = decision or _implied_decision(deliverable)
    reasons = _decision_reasons(deliverable, decision=decision, actor=actor)
    reasons.extend(_evidence_reasons(deliverable))
    reasons.extend(_fact_reasons(deliverable, fact_result=fact_result, facts_cache=facts_cache))

    return "".join(reasons)


def _implied_decision(deliverable: Deliverable) -> str:
    if deliverable.status == Deliverable.Status.PENDING_APPROVAL:
        return Approval.Decision.APPROVED

    return Approval.Decision.REQUESTED


def _decision_reasons(deliverable: Deliverable, *, decision: str, actor) -> list[str]:
    """承認（確定）そのものに固有のブロック理由。

    承認依頼の時点では止めない。人が確定本文を書くのは依頼の前後どちらでもよく、
    依頼を止めると「直せないのに承認へも進めない」行き止まりを作るため。
    """

    if decision != Approval.Decision.APPROVED:
        return []

    reasons: list[str] = []

    if not (deliverable.body or "").strip():
        # 確定本文が空＝人が 1 文字も確認していない AI 生成物。これを承認すると
        # 「人が確かめてから確定する」が成立しないまま確定情報になる。
        reasons.append("確定本文が空です。AI生成本文を確認し、確定本文として保存してください。")

    self_approval = _self_approval_reason(deliverable, actor)

    if self_approval:
        reasons.append(self_approval)

    return reasons


def _self_approval_reason(deliverable: Deliverable, actor) -> str:
    """自己承認（四眼原則違反）の理由。該当しなければ空文字。

    作成者・申請者のどちらとも突き合わせる。「承認依頼」→「承認」を同じ人が
    連続して押せる状態では、承認履歴が残っていても人の確認を経ていない。
    """

    actor_id = getattr(actor, "pk", None)

    if actor_id is None or not require_separate_approver():
        return ""

    if deliverable.created_by_id == actor_id:
        return "作成者は自分の成果物を承認できません。別の承認者へ依頼してください（四眼原則）。"

    requester_id = _requester_id(deliverable)

    if requester_id is not None and requester_id == actor_id:
        return "承認依頼をした本人は承認できません。別の承認者へ依頼してください（四眼原則）。"

    return ""


def _requester_id(deliverable: Deliverable):
    """いまの承認待ち状態を作った申請者。分からなければ None。"""

    requested = (
        deliverable.approvals.filter(decision=Approval.Decision.REQUESTED)
        .order_by("-created_at")
        .first()
    )

    return requested.actor_id if requested is not None else None


def _evidence_reasons(deliverable: Deliverable) -> list[str]:
    """根拠評価によるブロック理由。

    根拠評価そのものが無い成果物もここで止める。「評価していない」を
    「問題なし」と同じ扱いにすると、承認前ブロックが素通りになる。
    """

    if deliverable.can_request_approval:
        return []

    evidence = getattr(deliverable.agent_run, "evidence", None)

    if evidence is None:
        return ["根拠評価が未実施です。"]

    reasons: list[str] = []

    if evidence.has_conflict:
        reasons.append("根拠間に矛盾があります。")

    if evidence.recommendation == "ask_clarification":
        reasons.append(f"根拠が不足しています（確信度 {evidence.confidence:.2f}）。")

    missing = "／".join(str(item) for item in evidence.missing_information)

    if missing:
        reasons.append(f"不足情報: {missing}。")

    return reasons


def _fact_reasons(
    deliverable: Deliverable,
    *,
    fact_result: fact_check.FactCheckResult | None,
    facts_cache: dict | None,
) -> list[str]:
    """事実照合によるブロック理由。不一致が無ければ空リスト。

    照合できなかった記述は理由にしない。検査できていないことを根拠に
    承認を止めると、正しい成果物まで進まなくなるため。
    """

    result = fact_result or fact_check.check_deliverable(deliverable, facts_cache=facts_cache)
    reason = fact_check.reason_for(result)

    return [reason] if reason else []


def decide(*, deliverable: Deliverable, actor, decision: str, comment: str = "") -> DecisionResult:
    """成果物に対する承認アクションを 1 件記録し、状態を遷移させる。"""

    transition = _TRANSITIONS.get(decision)

    if transition is None:
        return DecisionResult(ok=False, message="不正な操作です。")

    allowed_from, next_status = transition

    if deliverable.status not in allowed_from:
        return DecisionResult(
            ok=False,
            message=f"「{deliverable.get_status_display()}」の成果物にはこの操作を行えません。",
        )

    if decision in _EVIDENCE_GATED:
        # 誰が何をしようとしているかまで渡す。承認可否は内容だけでは決まらず、
        # 「その人が承認してよいか」も含めて 1 か所で判断する。
        reason = blocking_reason(deliverable, decision=decision, actor=actor)

        if reason:
            return DecisionResult(ok=False, message=f"承認できません。{reason}")

    Approval.objects.create(
        deliverable=deliverable,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        decision=decision,
        comment=comment,
    )
    Deliverable.objects.filter(pk=deliverable.pk).update(status=next_status)

    label = Approval.Decision(decision).label

    return DecisionResult(ok=True, message=f"{deliverable.title} を「{label}」として記録しました。")


def withdraw_request(*, deliverable: Deliverable, actor, comment: str = "") -> bool:
    """承認待ちの成果物を下書きへ戻す（承認依頼の取り下げ）。

    承認待ちのまま本文を差し替えられると、承認者が読んだ内容と承認された内容が
    別物になる。本文を編集したらここを通し、版を繰り上げたうえで再依頼させる。
    """

    if deliverable.status != Deliverable.Status.PENDING_APPROVAL:
        return False

    Approval.objects.create(
        deliverable=deliverable,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        decision=Approval.Decision.WITHDRAWN,
        comment=comment,
    )
    Deliverable.objects.filter(pk=deliverable.pk).update(
        status=Deliverable.Status.DRAFT, version=deliverable.version + 1
    )

    return True
