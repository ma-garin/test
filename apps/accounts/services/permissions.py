"""操作単位の認可判定。

これまでの権限判定は「承認できるか（`User.can_approve`）」と「テナント管理者か
（`User.is_tenant_admin`）」の2つしかなく、案件単位では `ProjectMember` の
有無だけを見ていた。つまり *案件の中で誰が何をできるか* を表現できていない。

ここでは判定を次の1本へ集約する::

    can(user, Action.EDIT, obj)

**優先順位（上が強い）**

1. スーパーユーザー … 常に許可
2. テナント越境 … 無条件で拒否（許可より先に効く）
3. テナント管理者 … 自テナントの案件は案件役割に関わらず管理できる。
   管理者が案件役割で締め出されると、メンバー登録そのものができなくなり
   運用が復旧不能になるため、案件役割より優先する
4. **案件メンバーの役割** … 案件単位の権限はテナント単位より優先する
5. テナントロール … 案件に紐づかない操作のとき

**既存判定との関係**

`User.can_approve` / `User.is_tenant_admin` は残し、この関数から呼ぶ。
対応表（`settings.ROLE_PERMISSIONS`）を書き換えたときに、旧判定で通っていた
権限が黙って失われないよう、テナント側の判定は表と旧判定の和を取る。
表は「権限を増やす」方向にだけ効かせ、減らすときは旧判定側も直させる。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import PermissionDenied

from apps.accounts.constants import Action, ProjectRole

#: 全操作。テナント管理者・スーパーユーザーへ一括で与えるときに使う。
ALL_ACTIONS: frozenset[str] = frozenset(Action.values)


@dataclass(frozen=True)
class PermissionSet:
    """ある利用者が、ある対象に対して持つ操作の集合。

    画面へ「なぜその権限になったのか」を出せるよう、判定の根拠（`source`）も返す。
    真偽値だけ返すと、権限で困ったときに管理者が原因を追えない。
    """

    actions: frozenset[str]
    source: str
    role_display: str = ""

    def allows(self, action: str) -> bool:
        return str(action) in self.actions

    @property
    def can_view(self) -> bool:
        return self.allows(Action.VIEW)

    @property
    def can_edit(self) -> bool:
        return self.allows(Action.EDIT)

    @property
    def can_approve(self) -> bool:
        return self.allows(Action.APPROVE)

    @property
    def can_manage(self) -> bool:
        return self.allows(Action.MANAGE)

    @property
    def labels(self) -> list[str]:
        """許可されている操作の日本語表記。画面表示用。"""

        return [label for value, label in Action.choices if value in self.actions]


def tenant_id_of(obj):
    """対象が属するテナント。取れなければ None。

    案件配下のものは `project.tenant_id`、案件を持たないもの（テナント共通の文書など）は
    自分の `tenant_id` を見る。案件の有無で越境判定が抜けると、案件に紐づかない
    オブジェクトだけ他テナントから触れてしまう。
    """

    if obj is None:
        return None

    project = resolve_project(obj)

    if project is not None:
        return project.tenant_id

    return getattr(obj, "tenant_id", None)


def resolve_project(obj):
    """対象から案件を取り出す。

    案件配下のモデルは `project` を必ず持つ（`ProjectScopedModel`）。案件そのものか
    案件配下かをここで吸収し、呼び出し側が `obj.project` を書き分けずに済むようにする。
    """

    if obj is None:
        return None

    # モデルのインポートは遅延させる。accounts → projects の実インポートを
    # モジュール読み込み時に作ると、アプリ初期化順に依存して壊れやすい。
    from apps.projects.models import Project

    if isinstance(obj, Project):
        return obj

    project = getattr(obj, "project", None)

    return project if isinstance(project, Project) else None


def tenant_permissions(user) -> PermissionSet:
    """テナントロールに基づく権限。案件に紐づかない操作の判定に使う。"""

    if user is None or not getattr(user, "is_authenticated", False):
        return PermissionSet(actions=frozenset(), source="anonymous")

    if user.is_superuser:
        return PermissionSet(actions=ALL_ACTIONS, source="superuser", role_display="システム管理者")

    actions = set(settings.ROLE_PERMISSIONS.get(str(user.role), ()))

    # 旧判定を新表より弱くしない。表の書き換えで既存の承認権限が静かに
    # 失われると、承認が止まった理由を誰も追えなくなる。
    if user.can_approve:
        actions.update({Action.VIEW.value, Action.APPROVE.value})

    if user.is_tenant_admin:
        actions.update(ALL_ACTIONS)

    return PermissionSet(
        actions=frozenset(actions),
        source="tenant_role",
        role_display=user.get_role_display(),
    )


def project_role_of(user, project) -> str | None:
    """案件内の役割。メンバーでなければ None。"""

    if user is None or not getattr(user, "is_authenticated", False) or project is None:
        return None

    membership = (
        project.members.filter(user=user).only("role").first()
        if project.pk is not None
        else None
    )

    return membership.role if membership is not None else None


def permissions_for(user, obj=None) -> PermissionSet:
    """`user` が `obj` に対して持つ権限。`obj` が None ならテナント単位の権限。"""

    if user is None or not getattr(user, "is_authenticated", False):
        return PermissionSet(actions=frozenset(), source="anonymous")

    if user.is_superuser:
        return PermissionSet(actions=ALL_ACTIONS, source="superuser", role_display="システム管理者")

    project = resolve_project(obj)

    # テナント越境は許可より先に拒否する。ここを後ろに置くと、
    # 管理者判定が先に通ってしまい他テナントを触れる。
    #
    # 判定は案件の有無に関わらず行う。案件を持たない対象（テナント共通の文書など）を
    # 素通りさせると、そこだけ越境できる穴になる。
    owner_tenant_id = tenant_id_of(obj)

    if owner_tenant_id is not None:
        if user.tenant_id is None:
            # 所属テナントの無い利用者は、テナントに属するものを一切触れない。
            # 「所属が無いから判定を省く」は、無所属を最強の権限にしてしまう。
            return PermissionSet(actions=frozenset(), source="no_tenant")

        if owner_tenant_id != user.tenant_id:
            return PermissionSet(actions=frozenset(), source="cross_tenant")

    if project is None:
        return tenant_permissions(user)

    if user.is_tenant_admin:
        return PermissionSet(
            actions=ALL_ACTIONS, source="tenant_admin", role_display=user.get_role_display()
        )

    role = project_role_of(user, project)

    if role is None:
        # 案件メンバーでなければ案件配下は何もできない。
        # `projects_for()` も非メンバーを除外しており、判定を揃えている。
        return PermissionSet(actions=frozenset(), source="not_a_member")

    if role not in ProjectRole.values:
        # 想定外の役割（移行データ、手作業の UPDATE、空文字）。例外にせず拒否する。
        # ここで落とすと、壊れたデータ 1 件で案件の画面全体が 500 になる。
        return PermissionSet(actions=frozenset(), source="unknown_project_role")

    return PermissionSet(
        actions=frozenset(settings.PROJECT_ROLE_PERMISSIONS.get(str(role), ())),
        source="project_role",
        role_display=ProjectRole(role).label,
    )


def can(user, action: str, obj=None) -> bool:
    """`user` が `obj` に対して `action` を実行できるか。

    画面の表示制御と POST の検証で同じ関数を使うこと。表示だけで制御すると
    ボタンを隠しただけの「見た目の権限」になり、直接 POST で破られる。
    """

    return permissions_for(user, obj).allows(action)


def require(user, action: str, obj=None) -> None:
    """`can()` が False なら `PermissionDenied` を投げる。

    ビューの冒頭で1行呼ぶだけで POST 側の検証を強制できる形にしておく。
    """

    if not can(user, action, obj):
        raise PermissionDenied("この操作を行う権限がありません。")


@dataclass(frozen=True)
class MemberPermissionRow:
    """「メンバーと権限」カードの1行。誰が何をできるかをそのまま出す。"""

    member: object
    user: object
    role: str
    role_display: str
    permissions: PermissionSet

    @property
    def display_name(self) -> str:
        return str(self.user)


def member_permission_rows(project) -> list[MemberPermissionRow]:
    """案件メンバーと、その実効権限の一覧。

    役割の表だけを見せても「テナント管理者だから承認できる」といった実効権限は
    分からない。実際に `can()` を通した結果を出す。
    """

    if project is None or project.pk is None:
        return []

    members = project.members.select_related("user").order_by("role", "user__email")

    return [
        MemberPermissionRow(
            member=member,
            user=member.user,
            role=member.role,
            role_display=ProjectRole(member.role).label
            if member.role in ProjectRole.values
            else member.role,
            permissions=permissions_for(member.user, project),
        )
        for member in members
    ]
