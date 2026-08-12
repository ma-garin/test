"""変更影響分析の一覧と判断画面（UXP-10 / UXP-43）。

見るのは 3 点。判断待ちへ 1 クリックで絞り込めること、判断できない利用者に
「判断」を出さないこと、判断画面だけで判断材料が揃うこと。いずれかが欠けると、
判断者が一覧と入力画面を往復するか、押しても 403 になるボタンを踏む。
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.projects.models import ChangeRequest, Project, ProjectMember


class ChangeListScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.approver = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        # テナント側に承認権はあるが、この案件では「担当」なので判断できない人。
        self.member = User.objects.create_user(
            username="member",
            email="member@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="P-001", name="社内DX")
        ProjectMember.objects.create(
            project=self.project, user=self.member, role=ProjectRole.MEMBER
        )
        self.pending = ChangeRequest.objects.create(
            project=self.project,
            title="決済方式の変更",
            status=ChangeRequest.Status.PENDING_APPROVAL,
            estimated_effort_days=Decimal("3.0"),
            schedule_impact_days=2,
            impact_scope=["決済API", "受入テスト"],
        )
        self.draft = ChangeRequest.objects.create(
            project=self.project,
            title="帳票レイアウトの調整",
            status=ChangeRequest.Status.DRAFT,
        )

    def test_判断待ちのクイックビューで絞り込める(self) -> None:
        self.client.force_login(self.approver)

        response = self.client.get(reverse("dashboard:change"), {"status": "pending_approval"})

        self.assertEqual(response.status_code, 200)
        titles = [entry.change.title for entry in response.context["change_rows"]]
        self.assertEqual(titles, [self.pending.title])

        actives = [quick.label for quick in response.context["change_quick_views"] if quick.is_active]
        self.assertEqual(actives, ["判断待ち"])
        self.assertContains(response, 'data-quick-view="status=pending_approval"')

    def test_判断できない利用者には判断ボタンを出さず確認先を示す(self) -> None:
        self.client.force_login(self.member)

        response = self.client.get(reverse("dashboard:change"))

        self.assertNotContains(response, f'data-change-decide="{self.pending.pk}"')
        self.assertContains(response, f'data-change-decide-denied="{self.pending.pk}"')
        self.assertContains(response, "次の確認先")

    def test_判断できる利用者には判断待ちの行に判断ボタンを出す(self) -> None:
        self.client.force_login(self.approver)

        response = self.client.get(reverse("dashboard:change"))

        self.assertContains(response, f'data-change-decide="{self.pending.pk}"')

    def test_未入力の行を行内で警告する(self) -> None:
        self.client.force_login(self.approver)

        response = self.client.get(reverse("dashboard:change"))

        rows = {entry.change.pk: entry for entry in response.context["change_rows"]}
        self.assertEqual(rows[self.pending.pk].missing_labels, ())
        self.assertEqual(rows[self.draft.pk].missing_labels, ("工数", "日程影響", "影響範囲"))
        self.assertContains(response, f'data-change-missing="{self.draft.pk}"')

    def test_0件のとき登録の入口と判断フローを出す(self) -> None:
        ChangeRequest.objects.all().delete()
        self.client.force_login(self.approver)

        response = self.client.get(reverse("dashboard:change"))

        self.assertContains(response, "data-change-empty")
        self.assertContains(response, "判断フロー")
        self.assertContains(response, reverse("projects:change_create"))


class ChangeDecideScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.approver = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="P-001", name="社内DX")
        self.change = ChangeRequest.objects.create(
            project=self.project,
            title="決済方式の変更",
            description="クレジット決済を追加する",
            status=ChangeRequest.Status.PENDING_APPROVAL,
            estimated_effort_days=Decimal("3.0"),
        )
        self.client.force_login(self.approver)

    def test_判断画面に変更内容と工数の要約が出る(self) -> None:
        response = self.client.get(reverse("projects:change_decide", args=[self.change.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "クレジット決済を追加する")
        self.assertContains(response, "3 人日")
        self.assertContains(response, "data-decision-warning")
        self.assertContains(response, "取り消し")

    def test_未入力の判断材料を判断画面で名指しする(self) -> None:
        response = self.client.get(reverse("projects:change_decide", args=[self.change.pk]))

        self.assertContains(response, "data-change-missing")
        self.assertContains(response, "日程影響")
        self.assertContains(response, "影響範囲")
