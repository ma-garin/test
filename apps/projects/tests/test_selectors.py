"""案件アクセス範囲の分離テスト。

「案件データと検索対象文書のアクセス範囲を分離し、他案件への誤参照を防ぐ」は
再構築ブリーフの非機能要件。ここが崩れると他テナントのデータが見えるため、
参照経路（selectors）を直接検証する。
"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.projects.models import Project, ProjectMember
from apps.projects.selectors import projects_for


class ProjectsForTests(TestCase):
    def setUp(self) -> None:
        self.tenant_a = Tenant.objects.create(code="a", name="テナントA")
        self.tenant_b = Tenant.objects.create(code="b", name="テナントB")

        self.project_a1 = Project.objects.create(tenant=self.tenant_a, code="a1", name="A案件1")
        self.project_a2 = Project.objects.create(tenant=self.tenant_a, code="a2", name="A案件2")
        self.project_b1 = Project.objects.create(tenant=self.tenant_b, code="b1", name="B案件1")

        self.member = User.objects.create_user(
            username="member",
            email="member@example.com",
            password="x",
            tenant=self.tenant_a,
            role=Role.PMO,
        )
        ProjectMember.objects.create(project=self.project_a1, user=self.member)

    def test_一般ユーザーは所属案件のみ参照できる(self):
        result = projects_for(self.member, self.tenant_a)

        self.assertEqual(list(result), [self.project_a1])

    def test_他テナントを指定しても自テナント外は見えない(self):
        # tenant 引数を差し替えても、メンバーシップの絞り込みで空になる。
        self.assertEqual(list(projects_for(self.member, self.tenant_b)), [])

    def test_テナント管理者は自テナント全案件を参照できる(self):
        admin_user = User.objects.create_user(
            username="ta",
            email="ta@example.com",
            password="x",
            tenant=self.tenant_a,
            role=Role.TENANT_ADMIN,
        )
        result = projects_for(admin_user, self.tenant_a)

        self.assertCountEqual(result, [self.project_a1, self.project_a2])

    def test_テナント管理者でも他テナントは見えない(self):
        admin_user = User.objects.create_user(
            username="ta2",
            email="ta2@example.com",
            password="x",
            tenant=self.tenant_a,
            role=Role.TENANT_ADMIN,
        )

        self.assertNotIn(self.project_b1, projects_for(admin_user, self.tenant_a))

    def test_未認証は何も参照できない(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(list(projects_for(AnonymousUser(), self.tenant_a)), [])

    def test_論理削除した案件は含まれない(self):
        self.project_a1.soft_delete()

        self.assertEqual(list(projects_for(self.member, self.tenant_a)), [])
