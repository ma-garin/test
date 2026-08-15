"""Agentic トレースの閲覧範囲テスト。

トレースには相談の本文と根拠がそのまま残る。テナントで絞るだけでは、案件の
メンバーでない人が他案件の相談内容を全件読めてしまう。案件配下の実行は
案件メンバーの範囲（`projects_for()`）に揃っていること、一覧に出ない実行は
詳細も 404 になることを確かめる。

「見えなくなりすぎていないこと」（自分の案件・自分の実行・管理者は見える）も
併せて検証する。閲覧が過剰に狭いと、根拠を辿るという本来の目的が壊れる。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.agents.models import AgentRun
from apps.projects.models import Project, ProjectMember


class AgentRunVisibilityTests(TestCase):
    #: 混入したら一目で分かるよう、他案件のトレースだけに現れる語を使う。
    SECRET_INPUT = "他案件の値引き交渉について相談したい"

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.mine = Project.objects.create(tenant=self.tenant, code="p1", name="自分の案件")
        self.others = Project.objects.create(tenant=self.tenant, code="p2", name="他人の案件")

        self.member = self._user("member", Role.PMO)
        ProjectMember.objects.create(
            project=self.mine, user=self.member, role=ProjectRole.MEMBER
        )
        self.admin = self._user("admin", Role.TENANT_ADMIN)

        self.own_run = AgentRun.objects.create(
            tenant=self.tenant,
            project=self.mine,
            area=AgentRun.Area.PMO_CONSULTATION,
            user_input="自分の案件の遅延について相談したい",
        )
        self.other_run = AgentRun.objects.create(
            tenant=self.tenant,
            project=self.others,
            area=AgentRun.Area.PMO_CONSULTATION,
            user_input=self.SECRET_INPUT,
        )
        # 案件に紐づかない実行。本人だけが辿れる。
        self.my_tenant_run = AgentRun.objects.create(
            tenant=self.tenant,
            user=self.member,
            area=AgentRun.Area.RAG_CHAT,
            user_input="全社の標準について質問",
        )

    def _user(self, name: str, role: str) -> User:
        return User.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="test-password",
            tenant=self.tenant,
            role=role,
        )

    def test_非メンバーには他案件のトレースを見せない(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("agents:run_list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.SECRET_INPUT)

    def test_一覧に出ないトレースは詳細も開けない(self):
        """一覧から隠すだけでは、ID を直接叩かれたときに読まれてしまう。"""

        self.client.force_login(self.member)

        response = self.client.get(reverse("agents:run_detail", args=[self.other_run.pk]))

        self.assertEqual(response.status_code, 404)

    def test_自分の案件と自分の実行は見える(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("agents:run_list"))
        pks = {run.pk for run in response.context["runs"]}

        self.assertIn(self.own_run.pk, pks)
        self.assertIn(self.my_tenant_run.pk, pks)
        self.assertNotIn(self.other_run.pk, pks)

    def test_テナント管理者は運用のため全件を追える(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("agents:run_list"))

        self.assertEqual(response.context["page"].paginator.count, 3)
        self.assertContains(response, self.SECRET_INPUT)
