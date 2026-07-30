"""リスクの登録・更新・クローズ・課題化。

「リスクが顕在化したので課題として起票する」は PMO 実務で必ず起きる操作。
課題の作成とリスクの状態遷移が片方だけ成立すると台帳が食い違うため、
1 トランザクションで確定させる。
"""

from __future__ import annotations

from django.db import transaction

from apps.projects.models import Issue, Risk


def save_risk(form) -> Risk:
    """バリデーション済みフォームからリスクを確定する（新規・更新共通）。"""

    risk: Risk = form.save(commit=False)
    risk.save()

    return risk


def close_risk(risk: Risk) -> Risk:
    """リスクをクローズする。台帳は履歴として残すため物理削除しない。"""

    risk.status = Risk.Status.CLOSED
    risk.save(update_fields=["status", "updated_at"])

    return risk


@transaction.atomic
def promote_risk_to_issue(risk: Risk, *, issue_form) -> Issue:
    """リスクを課題へ転換し、リスクを「顕在化」状態にする。

    課題の案件はリスクの案件を引き継ぐ。フォーム側で案件を選ばせないのは、
    リスクと課題が別案件に分かれる事故を構造的に防ぐため。
    """

    issue: Issue = issue_form.save(commit=False)
    issue.project = risk.project

    if not issue.description:
        issue.description = risk.description

    issue.save()

    risk.status = Risk.Status.MATERIALIZED
    risk.save(update_fields=["status", "updated_at"])

    return issue
