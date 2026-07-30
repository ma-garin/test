"""ダッシュボード配下 7 画面の表示テスト。

「未実装」の 200 と「実データを出している」200 は外形が同じなので、
ステータスコードだけでは移植できたか判定できない。実データが本文に
現れることと、0 件のときに案内文が出ることの両方を確認する。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.dashboard.models import InterventionProposal, KpiMeasurement
from apps.projects.models import (
    ChangeRequest,
    Defect,
    Project,
    QualityMetric,
    Risk,
    Severity,
    WbsTask,
)

#: 担当画面の URL 名。テストの網羅漏れを防ぐためここで一覧化する。
SCREEN_NAMES = (
    "dashboard:tasks",
    "dashboard:progress",
    "dashboard:quality",
    "dashboard:risk",
    "dashboard:change",
    "dashboard:intervention",
    "dashboard:kpi",
)


class DashboardScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo-user",
            email="pmo-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(
            tenant=self.tenant, code="p1", name="基幹刷新プロジェクト"
        )
        self.client.force_login(self.user)

    def _create_data(self) -> None:
        today = timezone.localdate()

        WbsTask.objects.create(
            project=self.project,
            wbs_code="1.1",
            name="要件定義レビュー",
            owner="佐藤",
            status=WbsTask.Status.BLOCKED,
            planned_end=today - timedelta(days=3),
        )
        Risk.objects.create(
            project=self.project, title="要員離脱リスク", probability=5, impact=4
        )
        ChangeRequest.objects.create(
            project=self.project, title="帳票追加要望", schedule_impact_days=7
        )
        InterventionProposal.objects.create(
            project=self.project, title="レビュー体制の増強", confidence=0.8
        )
        Defect.objects.create(
            project=self.project, title="検索結果が0件になる", severity=Severity.HIGH
        )
        QualityMetric.objects.create(
            project=self.project,
            measured_on=today,
            metric_key="test_consumption_rate",
            metric_label="テスト消化率",
            value=72,
            threshold=80,
        )
        KpiMeasurement.objects.create(
            project=self.project,
            kind=KpiMeasurement.Kind.REPORT_HOURS,
            measured_on=today,
            baseline_value=10,
            actual_value=4,
            target_value=5,
            unit="h",
        )

    def test_全画面が200を返す(self) -> None:
        self._create_data()

        for name in SCREEN_NAMES:
            with self.subTest(screen=name):
                response = self.client.get(reverse(name))

                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "移植待ちの画面です")

    def test_実データが画面に出る(self) -> None:
        self._create_data()

        expectations = {
            "dashboard:tasks": "要件定義レビュー",
            "dashboard:risk": "要員離脱リスク",
            "dashboard:change": "帳票追加要望",
            "dashboard:intervention": "レビュー体制の増強",
            "dashboard:quality": "テスト消化率",
            "dashboard:kpi": "レポート作業時間",
            "dashboard:progress": "基幹刷新プロジェクト",
        }

        for name, expected in expectations.items():
            with self.subTest(screen=name):
                self.assertContains(self.client.get(reverse(name)), expected)

    def test_データが0件でも案内文を出す(self) -> None:
        for name in SCREEN_NAMES:
            with self.subTest(screen=name):
                response = self.client.get(reverse(name))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "ありません")

    def test_他テナントのデータは出さない(self) -> None:
        other = Tenant.objects.create(code="other", name="他社")
        other_project = Project.objects.create(tenant=other, code="x1", name="他社案件")
        Risk.objects.create(project=other_project, title="他社のリスク", probability=5, impact=5)

        response = self.client.get(reverse("dashboard:risk"))

        self.assertNotContains(response, "他社のリスク")

    def test_絞り込み条件が効く(self) -> None:
        self._create_data()

        response = self.client.get(reverse("dashboard:tasks"), {"status": "done"})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "要件定義レビュー")

    def test_不正な絞り込み値でも画面は壊れない(self) -> None:
        """選択肢に無い値が来ても 500 にせず、案内文つきの 200 を返す。

        URL を手で書き換えられた場合に画面が落ちると、原因の切り分けができない。
        """

        self._create_data()

        response = self.client.get(reverse("dashboard:tasks"), {"status": "not-a-status"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ありません")
