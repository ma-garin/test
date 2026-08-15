"""リスク・課題 CRUD のテナント分離とバリデーション。

「他案件への誤参照を防ぐ」は非機能要件なので、ID を直接叩いた場合に
404 になること（存在有無すら漏らさないこと）を経路ごとに検証する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.projects.models import Issue, Project, ProjectMember, Risk


class RiskIssueViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant_a = Tenant.objects.create(code="a", name="テナントA")
        self.tenant_b = Tenant.objects.create(code="b", name="テナントB")

        self.project_a = Project.objects.create(tenant=self.tenant_a, code="a1", name="A案件1")
        self.project_b = Project.objects.create(tenant=self.tenant_b, code="b1", name="B案件1")

        self.user = User.objects.create_user(
            username="member",
            email="member@example.com",
            password="x",
            tenant=self.tenant_a,
            role=Role.PMO,
        )
        ProjectMember.objects.create(project=self.project_a, user=self.user)
        self.client.force_login(self.user)

        self.risk_b = Risk.objects.create(project=self.project_b, title="他テナントのリスク")
        self.issue_b = Issue.objects.create(project=self.project_b, title="他テナントの課題")

    def _risk_payload(self, **overrides) -> dict:
        payload = {
            "project": str(self.project_a.pk),
            "title": "要員確保の遅れ",
            "description": "",
            "status": Risk.Status.IDENTIFIED,
            "impact": "4",
            "probability": "3",
            "mitigation": "増員を調整",
            "owner": "PMO",
            "due_date": "",
        }
        payload.update(overrides)

        return payload

    def _issue_payload(self, **overrides) -> dict:
        payload = {
            "project": str(self.project_a.pk),
            "title": "受入環境が未整備",
            "description": "",
            "status": Issue.Status.OPEN,
            "severity": "high",
            "owner": "PM",
            "due_date": "",
            "external_key": "PMO-1",
        }
        payload.update(overrides)

        return payload

    def test_リスクを新規作成できる(self):
        response = self.client.post(reverse("projects:risk_create"), self._risk_payload())

        self.assertEqual(response.status_code, 302)
        risk = Risk.objects.get(title="要員確保の遅れ")
        self.assertEqual(risk.project, self.project_a)
        self.assertEqual(risk.score, 12)

    def test_影響度が範囲外なら保存されない(self):
        response = self.client.post(
            reverse("projects:risk_create"), self._risk_payload(impact="6")
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Risk.objects.filter(title="要員確保の遅れ").exists())

    def test_発生確率が範囲外なら保存されない(self):
        response = self.client.post(
            reverse("projects:risk_create"), self._risk_payload(probability="0")
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Risk.objects.filter(title="要員確保の遅れ").exists())

    def test_参照できない案件を指定したリスクは作成できない(self):
        response = self.client.post(
            reverse("projects:risk_create"), self._risk_payload(project=str(self.project_b.pk))
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Risk.objects.filter(title="要員確保の遅れ").exists())

    def test_リスクを編集できる(self):
        risk = Risk.objects.create(project=self.project_a, title="旧タイトル")

        response = self.client.post(
            reverse("projects:risk_edit", args=[risk.pk]),
            self._risk_payload(title="新タイトル"),
        )

        self.assertEqual(response.status_code, 302)
        risk.refresh_from_db()
        self.assertEqual(risk.title, "新タイトル")

    def test_他テナントのリスクは編集画面を開けない(self):
        response = self.client.get(reverse("projects:risk_edit", args=[self.risk_b.pk]))

        self.assertEqual(response.status_code, 404)

    def test_他テナントのリスクはPOSTでも更新されない(self):
        response = self.client.post(
            reverse("projects:risk_edit", args=[self.risk_b.pk]),
            self._risk_payload(title="乗っ取り"),
        )

        self.assertEqual(response.status_code, 404)
        self.risk_b.refresh_from_db()
        self.assertEqual(self.risk_b.title, "他テナントのリスク")

    def test_リスクをクローズできる(self):
        risk = Risk.objects.create(project=self.project_a, title="クローズ対象")

        response = self.client.post(reverse("projects:risk_close", args=[risk.pk]))

        self.assertEqual(response.status_code, 302)
        risk.refresh_from_db()
        self.assertEqual(risk.status, Risk.Status.CLOSED)

    def test_リスクを課題へ転換すると顕在化になる(self):
        risk = Risk.objects.create(project=self.project_a, title="外部連携の仕様未確定")

        response = self.client.post(
            reverse("projects:risk_promote", args=[risk.pk]),
            {
                "title": "外部連携の仕様が確定せず結合できない",
                "description": "",
                "severity": "critical",
                "owner": "PM",
                "due_date": "",
                "external_key": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        risk.refresh_from_db()
        self.assertEqual(risk.status, Risk.Status.MATERIALIZED)
        issue = Issue.objects.get(title="外部連携の仕様が確定せず結合できない")
        self.assertEqual(issue.project, self.project_a)

    def test_他テナントのリスクは課題へ転換できない(self):
        response = self.client.post(
            reverse("projects:risk_promote", args=[self.risk_b.pk]),
            {
                "title": "乗っ取り",
                "description": "",
                "severity": "high",
                "owner": "",
                "due_date": "",
                "external_key": "",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Issue.objects.filter(title="乗っ取り").exists())

    def test_課題を新規作成できる(self):
        response = self.client.post(reverse("projects:issue_create"), self._issue_payload())

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Issue.objects.filter(title="受入環境が未整備", project=self.project_a).exists())

    def test_参照できない案件を指定した課題は作成できない(self):
        response = self.client.post(
            reverse("projects:issue_create"), self._issue_payload(project=str(self.project_b.pk))
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Issue.objects.filter(title="受入環境が未整備").exists())

    def test_他テナントの課題は編集画面を開けない(self):
        response = self.client.get(reverse("projects:issue_edit", args=[self.issue_b.pk]))

        self.assertEqual(response.status_code, 404)

    def test_課題をクローズすると解決日時が入る(self):
        issue = Issue.objects.create(project=self.project_a, title="クローズ対象")

        response = self.client.post(reverse("projects:issue_close", args=[issue.pk]))

        self.assertEqual(response.status_code, 302)
        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.Status.CLOSED)
        self.assertIsNotNone(issue.resolved_at)

    def test_課題一覧は参照できる案件だけを表示する(self):
        Issue.objects.create(project=self.project_a, title="自案件の課題")

        response = self.client.get(reverse("projects:issue_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "自案件の課題")
        self.assertNotContains(response, "他テナントの課題")

    def test_未認証はリスク作成画面へ入れない(self):
        self.client.logout()

        response = self.client.get(reverse("projects:risk_create"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])
