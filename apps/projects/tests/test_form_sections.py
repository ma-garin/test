"""入力フォームの区分表示テスト（UXP-39〜42）。

フォームは「並んでいるか」ではなく「区分と説明が画面に出ているか」で使えるかが
決まる。見出しが消える・区分の追加で入力欄が落ちる、のどちらも回帰として検出する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.projects.models import Defect, Issue, Project, ProjectMember, Risk, WbsTask


class FormSectionTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="a", name="テナントA")
        self.project = Project.objects.create(tenant=self.tenant, code="a1", name="A案件")
        self.user = User.objects.create_user(
            username="member",
            email="member@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.PMO,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.user, role=ProjectRole.PMO
        )
        self.client.force_login(self.user)

    def assert_all_fields_rendered(self, html: str, form) -> None:
        """区分に振り分け損ねた入力欄がないことを保証する。"""

        for name in form.fields:
            self.assertIn(f'name="{name}"', html, f"入力欄 {name} が画面から消えている")


class TaskFormSectionTests(FormSectionTestBase):
    """UXP-39: 基本情報・進捗・担当・危険な操作の3区分。"""

    def test_タスクフォームが3区分と全入力欄を表示する(self):
        task = WbsTask.objects.create(project=self.project, wbs_code="1.1", name="要件定義")
        response = self.client.get(reverse("projects:task_edit", args=[task.pk]))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("基本情報", html)
        self.assertIn("進捗・担当", html)
        self.assertIn("危険な操作", html)
        # アーカイブは主操作から離し、何が起きるかを文字で説明する。
        self.assertIn("このタスクをアーカイブする", html)
        self.assertIn("データは削除されません", html)
        self.assertLess(
            html.index("更新する"), html.index("危険な操作"), "危険な操作が主操作より前にある"
        )
        self.assert_all_fields_rendered(html, response.context["form"])

    def test_区分化後もタスクを保存できる(self):
        payload = {
            "project": str(self.project.pk),
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
        response = self.client.post(reverse("projects:task_create"), payload)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(WbsTask.objects.filter(project=self.project, wbs_code="2.1").exists())


class IssueFormSectionTests(FormSectionTestBase):
    """UXP-40: 事象・対応・追跡情報の3区分と、各区分の説明文。"""

    def test_課題フォームが3区分と説明を表示する(self):
        response = self.client.get(reverse("projects:issue_create"))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("事象（何が起きたか）", html)
        self.assertIn("対応（誰がいつまでに）", html)
        self.assertIn("追跡情報（どこで追うか）", html)
        self.assertIn("どれくらい重いのか", html)
        self.assertIn("いつまでに決着させるか", html)
        self.assertIn("外部の課題管理システム", html)
        self.assert_all_fields_rendered(html, response.context["form"])

    def test_区分化後も課題を保存できる(self):
        payload = {
            "project": str(self.project.pk),
            "title": "受入環境が未整備",
            "description": "",
            "status": Issue.Status.OPEN,
            "severity": "high",
            "owner": "PMO",
            "due_date": "",
            "external_key": "",
        }
        response = self.client.post(reverse("projects:issue_create"), payload)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Issue.objects.filter(project=self.project, title="受入環境が未整備").exists())


class RiskFormSectionTests(FormSectionTestBase):
    """UXP-41: スコアの計算式・対策が必要な条件・課題化の引き継ぎ内容。"""

    def test_リスクフォームがスコアの説明と課題化の影響を表示する(self):
        risk = Risk.objects.create(project=self.project, title="要員確保の遅れ")
        response = self.client.get(reverse("projects:risk_edit", args=[risk.pk]))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("影響度 × 発生確率", html)
        self.assertIn("16 以上は高リスク", html)
        self.assertIn("対応方針", html)
        # 課題化の説明は、転換リンクより前に出ていなければ意味がない。
        self.assertIn("引き継いだ課題", html)
        self.assertIn("状態が「顕在化」に変わって", html)
        self.assertLess(
            html.index("引き継いだ課題"),
            html.index("課題へ転換する</a>"),
            "課題化の説明が操作より後ろにある",
        )
        self.assert_all_fields_rendered(html, response.context["form"])

    def test_区分化後もリスクを保存できる(self):
        payload = {
            "project": str(self.project.pk),
            "title": "要員確保の遅れ",
            "description": "",
            "status": Risk.Status.IDENTIFIED,
            "impact": "4",
            "probability": "3",
            "mitigation": "増員を調整",
            "owner": "PMO",
            "due_date": "",
        }
        response = self.client.post(reverse("projects:risk_create"), payload)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Risk.objects.filter(project=self.project, title="要員確保の遅れ").exists())


class DefectFormSectionTests(FormSectionTestBase):
    """UXP-42: 発見内容・影響・解決確認の3区分とクローズ前チェック。"""

    def _defect(self) -> Defect:
        return Defect.objects.create(project=self.project, title="一覧の表示崩れ")

    def test_不具合フォームが3区分とクローズ確認項目を表示する(self):
        response = self.client.get(reverse("projects:defect_edit", args=[self._defect().pk]))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("発見内容", html)
        self.assertIn("影響", html)
        self.assertIn("解決確認", html)
        self.assertIn("「クローズ」にする前に", html)
        self.assertIn("再発しない", html)
        self.assert_all_fields_rendered(html, response.context["form"])

    def test_区分化後も不具合を保存できる(self):
        defect = self._defect()
        payload = {
            "project": str(self.project.pk),
            "title": "一覧の表示崩れ（修正版）",
            "status": defect.status,
            "severity": defect.severity,
            "phase": defect.phase,
            "description": "幅が狭い環境で列が重なる",
            "detected_on": "",
            "closed_on": "",
        }
        response = self.client.post(reverse("projects:defect_edit", args=[defect.pk]), payload)

        self.assertEqual(response.status_code, 302)
        defect.refresh_from_db()
        self.assertEqual(defect.title, "一覧の表示崩れ（修正版）")
