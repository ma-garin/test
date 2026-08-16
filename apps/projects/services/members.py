"""案件メンバーの登録・変更・解除。

「誰を案件に入れるか」は権限そのものなので、ビューへ直接書かずここへ集約する。
テナント越境（他テナントの利用者を案件へ入れる）は、ここで必ず落とす。
画面のセレクトボックスを絞るだけでは、POST を直接叩かれると通ってしまう。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import QuerySet

from apps.accounts.constants import ProjectRole
from apps.accounts.models import User
from apps.projects.models import Project, ProjectMember


@dataclass(frozen=True)
class MemberResult:
    """操作結果。画面へ出すメッセージまで含めて返す。"""

    ok: bool
    message: str
    member: ProjectMember | None = None


def assignable_users_for(project: Project) -> QuerySet[User]:
    """その案件へ割り当ててよい利用者。

    案件のテナントに所属する利用者だけ。テナント未所属の利用者は、
    どのテナントのデータを見せてよいか決められないため対象外にする。
    """

    if project is None or project.tenant_id is None:
        return User.objects.none()

    return User.objects.filter(tenant_id=project.tenant_id, is_active=True).order_by("email")


def assign_member(
    project: Project, *, user: User, role: str, role_label: str = ""
) -> MemberResult:
    """メンバーを登録、または役割を変更する。"""

    if project is None or user is None:
        return MemberResult(ok=False, message="案件または利用者が指定されていません。")

    if user.tenant_id != project.tenant_id:
        # テナント越境。画面で選べなくても POST では来る前提で必ず落とす。
        return MemberResult(ok=False, message="他テナントの利用者は案件へ登録できません。")

    if str(role) not in ProjectRole.values:
        return MemberResult(ok=False, message="役割の指定が不正です。")

    member, created = ProjectMember.objects.update_or_create(
        project=project,
        user=user,
        defaults={"role": role, "role_label": role_label or ProjectRole(role).label},
    )
    verb = "登録" if created else "更新"

    return MemberResult(
        ok=True,
        message=f"{user} を「{ProjectRole(role).label}」として{verb}しました。",
        member=member,
    )


def remove_member(project: Project, *, member_pk) -> MemberResult:
    """メンバーを解除する。

    削除対象は必ず「その案件配下」から引く。ID だけで削除すると、
    他案件のメンバー行を消せてしまう。
    """

    member = ProjectMember.objects.filter(project=project, pk=member_pk).first()

    if member is None:
        return MemberResult(ok=False, message="対象のメンバーが見つかりません。")

    label = str(member.user)
    member.delete()

    return MemberResult(ok=True, message=f"{label} を案件メンバーから解除しました。")
