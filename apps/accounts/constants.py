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


class ProjectRole(models.TextChoices):
    """案件の中での役割（要件 #30）。

    テナントロール（`Role`）が「この人はそもそも何ができるか」を決め、
    案件ロールは「この案件では何をしてよいか」を決める。

    **案件ロールは権限を狭めるだけで、広げない。** 参照のみのテナントロールの人が
    案件責任者に任命されたら承認できる、という抜け道を作らないため。
    テナント側で承認権が無い人は、どの案件でも承認できない。
    """

    OWNER = "owner", "案件責任者"
    PMO = "pmo", "案件PMO"
    MEMBER = "member", "担当"
    VIEWER = "viewer", "参照のみ"


#: 案件のデータを更新できる案件ロール。
PROJECT_EDITOR_ROLES = (ProjectRole.OWNER, ProjectRole.PMO, ProjectRole.MEMBER)

#: 案件の中で承認・判断を行える案件ロール。テナント側の承認権と AND で効く。
PROJECT_APPROVER_ROLES = (ProjectRole.OWNER, ProjectRole.PMO)
