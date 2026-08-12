"""承認ゲートの業務ロジック。

「根拠が不十分なら承認へ進ませない」がこのシステムの中核なので、その判定と
状態遷移をビューから切り離してここに置く。画面は結果メッセージを出すだけにする。

状態はモデルを直接書き換えず `QuerySet.update()` で更新する。渡された
インスタンスを副作用で変えないほうが、呼び出し側の想定が壊れにくい。
"""

from __future__ import annotations

from dataclasses import dataclass

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


def blocking_reason(
    deliverable: Deliverable,
    *,
    fact_result: fact_check.FactCheckResult | None = None,
    facts_cache: dict | None = None,
) -> str:
    """承認へ進めない理由。進めるなら空文字。

    画面でボタンを無効化するだけでは利用者が理由を判断できないため、
    根拠評価のどの項目で止まっているかを文章で返す。

    根拠評価に加えて、本文の数値・固有名詞が実データと食い違っていないか
    （事実照合）もここで見る。ゲートを 2 か所に分けると、片方だけを通って
    承認される抜け道ができるため、承認可否の判断はこの関数に集約する。
    `fact_result` は一覧などで既に照合済みの結果を渡し、再計算を避けるため。
    """

    reasons = _fact_reasons(deliverable, fact_result=fact_result, facts_cache=facts_cache)

    if deliverable.can_request_approval:
        return "".join(reasons)

    evidence = getattr(deliverable.agent_run, "evidence", None)

    if evidence is None:
        return "根拠評価が未実施です。" + "".join(reasons)

    evidence_reasons: list[str] = []

    if evidence.has_conflict:
        evidence_reasons.append("根拠間に矛盾があります")

    if evidence.recommendation == "ask_clarification":
        evidence_reasons.append(f"根拠が不足しています（確信度 {evidence.confidence:.2f}）")

    missing = "／".join(str(item) for item in evidence.missing_information)

    if missing:
        evidence_reasons.append(f"不足情報: {missing}")

    return "。".join(evidence_reasons) + "。" + "".join(reasons)


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
        reason = blocking_reason(deliverable)

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
