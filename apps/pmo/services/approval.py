"""承認ゲートの業務ロジック。

「根拠が不十分なら承認へ進ませない」がこのシステムの中核なので、その判定と
状態遷移をビューから切り離してここに置く。画面は結果メッセージを出すだけにする。

状態はモデルを直接書き換えず `QuerySet.update()` で更新する。渡された
インスタンスを副作用で変えないほうが、呼び出し側の想定が壊れにくい。
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.pmo.models import Approval, Deliverable

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


def blocking_reason(deliverable: Deliverable) -> str:
    """承認へ進めない理由。進めるなら空文字。

    画面でボタンを無効化するだけでは利用者が理由を判断できないため、
    根拠評価のどの項目で止まっているかを文章で返す。
    """

    if deliverable.can_request_approval:
        return ""

    evidence = getattr(deliverable.agent_run, "evidence", None)

    if evidence is None:
        return "根拠評価が未実施です。"

    reasons: list[str] = []

    if evidence.has_conflict:
        reasons.append("根拠間に矛盾があります")

    if evidence.recommendation == "ask_clarification":
        reasons.append(f"根拠が不足しています（確信度 {evidence.confidence:.2f}）")

    missing = "／".join(str(item) for item in evidence.missing_information)

    if missing:
        reasons.append(f"不足情報: {missing}")

    return "。".join(reasons) + "。"


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

    if decision in _EVIDENCE_GATED and not deliverable.can_request_approval:
        return DecisionResult(ok=False, message=f"承認できません。{blocking_reason(deliverable)}")

    Approval.objects.create(
        deliverable=deliverable,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        decision=decision,
        comment=comment,
    )
    Deliverable.objects.filter(pk=deliverable.pk).update(status=next_status)

    label = Approval.Decision(decision).label

    return DecisionResult(ok=True, message=f"{deliverable.title} を「{label}」として記録しました。")
