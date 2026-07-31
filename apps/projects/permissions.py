"""案件単位の権限判定（要件 #30）。

判定を 2 段にする。

1. **テナントロール**（`User.role`）… そもそも何ができる人か
2. **案件ロール**（`ProjectMember.role`）… この案件では何をしてよいか

案件ロールは 1 を**狭めるだけ**で、広げない。参照専用の利用者を案件責任者に
任命したら承認できてしまう、という抜け道を作らないため。

権限判定をビューへ散らさない。散らすと必ずどこかで書き忘れる。
"""

from __future__ import annotations

from apps.accounts.constants import (
    PROJECT_APPROVER_ROLES,
    PROJECT_EDITOR_ROLES,
    ProjectRole,
    Role,
)

#: 案件メンバーでなくても全案件を扱えるテナントロール。運用上の逃げ道として残す。
TENANT_WIDE_ROLES = (Role.TENANT_ADMIN, Role.SYSTEM_ADMIN)


def _is_tenant_wide(user) -> bool:
    return bool(
        getattr(user, "is_superuser", False) or getattr(user, "role", "") in TENANT_WIDE_ROLES
    )


def project_role(user, project) -> str | None:
    """この利用者の案件ロール。メンバーでなければ None。

    テナント管理者は全案件を見られるが、メンバーではない。その場合は
    案件責任者と同等として扱う（`TENANT_WIDE_ROLES`）。
    """

    if user is None or not getattr(user, "is_authenticated", False) or project is None:
        return None

    from apps.projects.models import ProjectMember

    membership = ProjectMember.objects.filter(project=project, user=user).first()

    if membership is not None:
        return membership.role

    return ProjectRole.OWNER if _is_tenant_wide(user) else None


def can_view_project(user, project) -> bool:
    return project_role(user, project) is not None


def can_edit_project(user, project) -> bool:
    """案件のデータ（タスク・リスク・課題・変更・不具合）を更新できるか。"""

    role = project_role(user, project)

    if role is None:
        return False

    # テナント側が参照のみなら、案件ロールが何であっても書けない。
    if getattr(user, "role", "") == Role.VIEWER and not _is_tenant_wide(user):
        return False

    return role in PROJECT_EDITOR_ROLES


def can_approve_in_project(user, project) -> bool:
    """この案件で承認・判断を行えるか。

    テナント側の承認権（`User.can_approve`）と、案件ロールの両方が要る。
    """

    if not getattr(user, "can_approve", False):
        return False

    role = project_role(user, project)

    return role is not None and role in PROJECT_APPROVER_ROLES


def editable_projects_for(user, projects):
    """書き込み対象として選べる案件だけに絞る。

    フォームの選択肢をここで絞ることで、「選べたのに保存できない」を防ぐ。
    """

    if _is_tenant_wide(user):
        return projects

    if getattr(user, "role", "") == Role.VIEWER:
        return projects.none()

    return projects.filter(members__user=user, members__role__in=PROJECT_EDITOR_ROLES)


def approval_denied_reason(user, project) -> str:
    """承認できない理由。画面とサービスで同じ文言を使う。"""

    if can_approve_in_project(user, project):
        return ""

    if not getattr(user, "can_approve", False):
        return "承認権限のあるロールではありません。"

    if project_role(user, project) is None:
        return "この案件のメンバーではありません。"

    return "この案件では承認できる役割ではありません（案件責任者・案件PMOのみ）。"
