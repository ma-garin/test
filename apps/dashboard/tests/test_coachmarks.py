"""初見の利用者向けの案内（コーチマーク・説明ツールチップ）の回帰テスト。

画面の見方を説明する部品は「置いたつもり」で欠落しても、既存の集計テストでは
落ちない。画面ごとに、案内が出ること・閉じられること・用語の説明が読めることを
固定する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.projects.models import Project, ProjectMember, WbsTask


class CoachmarkTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="coach", name="案内確認テナント")
        self.user = User.objects.create_user(
            username="coach-user",
            email="coach-user@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.project = Project.objects.create(
            tenant=self.tenant,
            code="coach-project",
            name="案内確認案件",
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectRole.OWNER,
        )
        WbsTask.objects.create(
            project=self.project,
            wbs_code="1.1",
            name="案内確認タスク",
        )
        self.client.force_login(self.user)

    def assert_coachmark(self, response, coach_key: str) -> None:
        """コーチマークが出ており、閉じる手段があることを確認する。"""
        self.assertContains(response, f'data-coach="{coach_key}"')
        self.assertContains(response, "この画面の見方")
        self.assertContains(response, "data-coach-close")
        self.assertContains(response, "この案内を閉じる")

    def test_管制ダッシュボードは見方とヘルススコアの意味を示す(self) -> None:
        response = self.client.get(reverse("dashboard:control"))

        self.assert_coachmark(response, "control")
        self.assertContains(response, "まず「次にやること」を見ます")
        # ツールチップは id ではなく読み上げ名で説明を持つ。
        # 日本語ラベルは slugify が空になり、id では 2 個目以降が衝突するため。
        self.assertContains(response, "0〜100の健全度")
        self.assertContains(response, "人が採用または却下を決めるまで実行されません")

    def test_進捗予測は計画実績差の正負の向きを示す(self) -> None:
        response = self.client.get(reverse("dashboard:progress"))

        self.assert_coachmark(response, "progress")
        self.assertContains(response, "マイナスは計画より遅れている、プラスは計画より進んでいる")
        self.assertContains(response, "期限が近いのに進捗が足りていないタスク")

    def test_予兆検知はプレビューと保存実行の違いを示す(self) -> None:
        response = self.client.get(reverse("dashboard:detection"))

        self.assert_coachmark(response, "detection")
        self.assertContains(response, "アラートも介入提案もまだ作られていません")
        self.assertContains(response, "危険とも安全とも言えない項目です")

    def test_ライブ着地予測は確信度と算定不能と営業日を示す(self) -> None:
        response = self.client.get(reverse("forecast:live"))

        self.assert_coachmark(response, "forecast_live")
        self.assertContains(response, "入力の品質から機械的に決めます")
        self.assertContains(response, "日数計算の前提そのものが欠けている状態")
        self.assertContains(response, "ずれを営業日で数えます")

    def test_報告下書きは通知対象と未確認事項の扱いを示す(self) -> None:
        response = self.client.get(reverse("forecast:report"))

        self.assert_coachmark(response, "forecast_report")
        self.assertContains(response, "この画面が作るのは下書きだけです")
        self.assertContains(response, "悪化と算定不能化だけを数えます")

    def test_案内は画面ごとに別のキーで閉じ分けられる(self) -> None:
        keys = {
            "dashboard:control": "control",
            "dashboard:progress": "progress",
            "dashboard:detection": "detection",
            "forecast:live": "forecast_live",
            "forecast:report": "forecast_report",
        }
        for url_name, coach_key in keys.items():
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'data-coach="{coach_key}"')
