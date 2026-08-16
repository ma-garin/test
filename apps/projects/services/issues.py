"""課題の登録・更新・クローズ。"""

from __future__ import annotations

from django.utils import timezone

from apps.projects.models import Issue


def save_issue(form) -> Issue:
    """バリデーション済みフォームから課題を確定する（新規・更新共通）。"""

    issue: Issue = form.save(commit=False)

    if issue.status in {Issue.Status.RESOLVED, Issue.Status.CLOSED} and issue.resolved_at is None:
        issue.resolved_at = timezone.now()

    issue.save()

    return issue


def close_issue(issue: Issue) -> Issue:
    """課題をクローズする。台帳は履歴として残すため物理削除しない。"""

    issue.status = Issue.Status.CLOSED
    issue.resolved_at = issue.resolved_at or timezone.now()
    issue.save(update_fields=["status", "resolved_at", "updated_at"])

    return issue
