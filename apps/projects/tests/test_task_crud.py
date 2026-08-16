"""WBS タスク CRUD のテナント分離テスト。

他テナントのタスク ID を直接叩いても 404 になること、フォームの案件選択肢が
参照可能な案件に限られることを検証する。ここが崩れると越境更新が通る。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.projects.models import Project, ProjectMember, WbsTask


class TaskCrudTests(TestCase):
    def setUp(self) -> None:
        self.tenant_a = Tenant.objects.create(code="a", name="テナントA")
        self.tenant_b = Tenant.objects.create(code="b", name="テナントB")

        self.project_a = Project.objects.create(tenant=self.tenant_a, code="a1", name="A案件")
        self.project_b = Project.objects.create(tenant=self.tenant_b, code="b1", name="B案件")

        self.user = User.objects.create_user(
            username="member",
            email="member@example.com",
            password="x",
            tenant=self.tenant_a,
            role=Role.PMO,
        )
        ProjectMember.objects.create(project=self.project_a, user=self.user)

        self.task_a = WbsTask.objects.create(
            project=self.project_a, wbs_code="1.1", name="要件定義"
        )
        self.task_b = WbsTask.objects.create(
            project=self.project_b, wbs_code="1.1", name="他テナントのタスク"
        )

        self.client.force_login(self.user)

    def _payload(self, **overrides) -> dict:
        payload = {
            "project": str(self.project_a.pk),
            "wbs_code": "2.1",
            "name": "基本設計",
            "owner": "山田",
            "planned_start": "",
            "planned_end": "2026-08-31",
            "progress_percent": "20",
            "priority": "high",
            "status": "in_progress",
            "follow_up_state": "watching",
            "next_action": "レビュー依頼",
            "ball_holder": "顧客",
            "evidence_note": "議事録より",
        }
        payload.update(overrides)

        return payload

    def test_タスクを新規作成できる(self):
        response = self.client.post(reverse("projects:task_create"), self._payload())

        self.assertEqual(response.status_code, 302)
        created = WbsTask.objects.get(project=self.project_a, wbs_code="2.1")
        self.assertEqual(created.name, "基本設計")
        self.assertEqual(created.ball_holder, "顧客")

    def test_他テナントの案件を指定した作成は拒否される(self):
        response = self.client.post(
            reverse("projects:task_create"),
            self._payload(project=str(self.project_b.pk)),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(WbsTask.objects.filter(project=self.project_b, wbs_code="2.1").exists())

    def test_進捗率が範囲外なら保存されない(self):
        response = self.client.post(
            reverse("projects:task_create"), self._payload(progress_percent="120")
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(WbsTask.objects.filter(wbs_code="2.1").exists())

    def test_案件内でWBS番号が重複したら保存されない(self):
        response = self.client.post(reverse("projects:task_create"), self._payload(wbs_code="1.1"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(WbsTask.objects.filter(project=self.project_a, wbs_code="1.1").count(), 1)

    def test_タスクを編集できる(self):
        response = self.client.post(
            reverse("projects:task_edit", args=[self.task_a.pk]),
            self._payload(wbs_code="1.1", name="要件定義（改訂）"),
        )

        self.assertEqual(response.status_code, 302)
        self.task_a.refresh_from_db()
        self.assertEqual(self.task_a.name, "要件定義（改訂）")

    def test_タスクをアーカイブしても物理削除しない(self):
        response = self.client.post(reverse("projects:task_archive", args=[self.task_a.pk]))

        self.assertEqual(response.status_code, 302)
        self.task_a.refresh_from_db()
        self.assertEqual(self.task_a.status, WbsTask.Status.ARCHIVED)
        self.assertTrue(WbsTask.objects.filter(pk=self.task_a.pk).exists())

    def test_他テナントのタスクは詳細を開けない(self):
        response = self.client.get(reverse("projects:task_detail", args=[self.task_b.pk]))

        self.assertEqual(response.status_code, 404)

    def test_他テナントのタスクは編集画面を開けない(self):
        response = self.client.get(reverse("projects:task_edit", args=[self.task_b.pk]))

        self.assertEqual(response.status_code, 404)

    def test_他テナントのタスクはアーカイブできない(self):
        response = self.client.post(reverse("projects:task_archive", args=[self.task_b.pk]))

        self.assertEqual(response.status_code, 404)
        self.task_b.refresh_from_db()
        self.assertNotEqual(self.task_b.status, WbsTask.Status.ARCHIVED)

    def test_未ログインならログインへ誘導される(self):
        self.client.logout()
        response = self.client.get(reverse("projects:task_create"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_案件選択肢は参照できる案件に限られる(self):
        response = self.client.get(reverse("projects:task_create"))

        self.assertEqual(response.status_code, 200)
        choices = list(response.context["form"].fields["project"].queryset)
        self.assertEqual(choices, [self.project_a])
