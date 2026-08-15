"""テナント境界と、壊れたデータでの権限判定。

権限判定は「許可を出す」より「拒否を漏らさない」方が難しい。ここでは
*許可されないはずの経路*だけを集めて固定する。
"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.constants import Action, ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.accounts.services.permissions import can, permissions_for
from apps.documents.models import Document, FileType
from apps.projects.models import Project, ProjectMember


class CrossTenantTests(TestCase):
    def setUp(self) -> None:
        self.home = Tenant.objects.create(code="home", name="ホーム")
        self.other = Tenant.objects.create(code="other", name="よその会社")
        self.user = User.objects.create_user(
            username="pm", email="pm@example.com", password="x", tenant=self.home, role=Role.PMO
        )
        self.foreign_project = Project.objects.create(
            tenant=self.other, code="other-1", name="よその案件"
        )

    def test_他テナントの案件は何もできない(self):
        self.assertEqual(permissions_for(self.user, self.foreign_project).source, "cross_tenant")
        self.assertFalse(can(self.user, Action.VIEW, self.foreign_project))

    def test_案件を持たない他テナントの対象も拒否する(self):
        """案件に紐づかない対象（テナント共通の文書）で越境チェックが抜けていた。

        `resolve_project()` が None を返すと、以前はテナントロールの判定へ落ちて
        しまい、他テナントのものでも自分のロール権限で許可されていた。
        """

        document = Document.objects.create(
            tenant=self.other, title="よその設計書", file_type=FileType.PDF
        )

        self.assertEqual(permissions_for(self.user, document).source, "cross_tenant")
        self.assertFalse(can(self.user, Action.VIEW, document))

    def test_所属テナントの無い利用者はテナントの対象を触れない(self):
        """無所属を「判定を省く」扱いにすると、無所属が最強の権限になる。"""

        drifter = User.objects.create_user(
            username="drifter", email="drifter@example.com", password="x", tenant=None, role=Role.PMO
        )
        document = Document.objects.create(
            tenant=self.home, title="自社の設計書", file_type=FileType.PDF
        )

        self.assertEqual(permissions_for(drifter, document).source, "no_tenant")
        self.assertFalse(can(drifter, Action.VIEW, document))
        self.assertFalse(can(drifter, Action.EDIT, self.foreign_project))


class BrokenProjectRoleTests(TestCase):
    """役割に想定外の値が入っていても、画面を落とさず拒否する。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件")
        self.user = User.objects.create_user(
            username="member",
            email="member@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.CHANGE_MANAGER,
        )

    def test_不正な役割は例外にせず拒否する(self):
        membership = ProjectMember.objects.create(
            project=self.project, user=self.user, role=ProjectRole.MEMBER
        )
        # 移行データや手作業の UPDATE で入りうる値。バリデーションは通らない経路。
        ProjectMember.objects.filter(pk=membership.pk).update(role="")

        result = permissions_for(self.user, self.project)

        self.assertEqual(result.source, "unknown_project_role")
        self.assertFalse(result.can_view)

    def test_メンバーでなければ案件配下は何もできない(self):
        self.assertEqual(permissions_for(self.user, self.project).source, "not_a_member")
