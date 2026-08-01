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


class ProjectRole(models.TextChoices):
    """案件内の役割。

    `ProjectMember.role_label` は自由文字列で、表記ゆれ（「PMO」「PMO担当」）を
    権限判定に使えない。判定に使う識別子はここで閉じた集合にする。
    """

    PROJECT_MANAGER = "pm", "PM"
    PMO = "pmo", "PMO"
    MEMBER = "member", "メンバー"
    VIEWER = "viewer", "参照"


#: `role_label`（自由文字列）から `ProjectRole` を推定する対応。
#: 既存データを役割へ移行するときだけ使う。判定には使わない。
ROLE_LABEL_HINTS: tuple[tuple[str, str], ...] = (
    ("PMO", ProjectRole.PMO),
    ("pmo", ProjectRole.PMO),
    ("PM", ProjectRole.PROJECT_MANAGER),
    ("PL", ProjectRole.PROJECT_MANAGER),
    ("プロジェクトマネージャ", ProjectRole.PROJECT_MANAGER),
    ("参照", ProjectRole.VIEWER),
    ("閲覧", ProjectRole.VIEWER),
    ("オブザーバ", ProjectRole.VIEWER),
)
