"""ロール定義。

旧実装の `access_flow.ROLE_OPTIONS` を、権限判定に使える識別子付きへ整理した。
"""

from django.db import models


class Role(models.TextChoices):
    PMO = "pmo", "PMO担当"
    PROJECT_MANAGER = "pm", "PM・PL"
    QUALITY_MANAGER = "quality", "品質責任者"
    CHANGE_MANAGER = "change", "変更管理者"
    VIEWER = "viewer", "参照のみ"
    TENANT_ADMIN = "tenant_admin", "テナント管理者"
    SYSTEM_ADMIN = "system_admin", "システム管理者"


#: 承認（HITL）を実行できるロール。
APPROVER_ROLES = (
    Role.PMO,
    Role.PROJECT_MANAGER,
    Role.QUALITY_MANAGER,
    Role.TENANT_ADMIN,
    Role.SYSTEM_ADMIN,
)


class Action(models.TextChoices):
    """操作単位の権限。

    ロールは「肩書き」であって権限ではない。画面ごとに `role == X` を書き並べると
    ロールが増えるたび全画面を直すことになり、必ずどこかで漏れる。
    先に操作を4つへ固定し、ロール→操作の対応表（settings）で判定する。
    """

    VIEW = "view", "閲覧"
    EDIT = "edit", "編集"
    APPROVE = "approve", "承認"
    MANAGE = "manage", "管理"
