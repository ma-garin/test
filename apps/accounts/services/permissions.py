"""操作単位の認可判定。

判定を次の1本へ集約する::

    can(user, Action.EDIT)

**優先順位（上が強い）**

1. スーパーユーザー … 常に許可
2. テナント管理者 … 自テナント内はロールに関わらず全操作を許可
3. テナントロール … 対応表（`settings.ROLE_PERMISSIONS`）で判定する

**既存判定との関係**

`User.can_approve` / `User.is_tenant_admin` は残し、この関数から呼ぶ。
対応表を書き換えたときに、旧判定で通っていた権限が黙って失われないよう、
テナント側の判定は表と旧判定の和を取る。表は「権限を増やす」方向にだけ
効かせ、減らすときは旧判定側も直させる。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import PermissionDenied

from apps.accounts.constants import Action

#: 全操作。テナント管理者・スーパーユーザーへ一括で与えるときに使う。
ALL_ACTIONS: frozenset[str] = frozenset(Action.values)


@dataclass(frozen=True)
class PermissionSet:
    """ある利用者が持つ操作の集合。

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


def permissions_for(user) -> PermissionSet:
    """`user` が持つ操作の集合。テナントロールに基づいて判定する。"""

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


def can(user, action: str) -> bool:
    """`user` が `action` を実行できるか。

    画面の表示制御と POST の検証で同じ関数を使うこと。表示だけで制御すると
    ボタンを隠しただけの「見た目の権限」になり、直接 POST で破られる。
    """

    return permissions_for(user).allows(action)


def require(user, action: str) -> None:
    """`can()` が False なら `PermissionDenied` を投げる。

    ビューの冒頭で1行呼ぶだけで POST 側の検証を強制できる形にしておく。
    """

    if not can(user, action):
        raise PermissionDenied("この操作を行う権限がありません。")
