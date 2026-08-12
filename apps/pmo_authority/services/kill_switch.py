"""緊急停止スイッチの判定（安全施策.md SC-08）。

five 段階（global/tenant/project/connector/operation）のいずれかが
作動中なら、対象の実行を拒否する。Broker は capability を実行する前に
必ずこれを呼ぶ（SC-08: 「kill switch は Authority と Broker の両方で
毎回確認する」）。
"""

from __future__ import annotations

from uuid import UUID

from django.db.models import Q

from apps.pmo_authority.models import KillSwitch, KillSwitchScope


def check_kill_switches(
    *, tenant_id: UUID, project_id: UUID, connector: str, operation: str
) -> str | None:
    """該当する範囲のkill switchが作動中なら、その理由を返す。無ければNone。

    5段階すべてを確認する（どれか1つでも作動中なら拒否）。
    """

    query = (
        Q(scope=KillSwitchScope.GLOBAL)
        | Q(scope=KillSwitchScope.TENANT, tenant_id=tenant_id)
        | Q(scope=KillSwitchScope.PROJECT, project_id=project_id)
        | Q(scope=KillSwitchScope.CONNECTOR, connector=connector)
        | Q(scope=KillSwitchScope.OPERATION, operation=operation)
    )
    tripped = KillSwitch.objects.filter(is_tripped=True).filter(query).order_by("scope").first()
    if tripped is None:
        return None

    reason = tripped.reason or "理由未記録"
    return f"{tripped.get_scope_display()}のkill switchが作動中のため実行を拒否しました（理由: {reason}）。"
