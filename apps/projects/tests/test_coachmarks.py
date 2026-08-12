"""各画面の「この画面の見方」と説明ツールチップの配置を検査する。

コーチマークもツールチップも共通部品（`templates/partials/`）で実装済みで、
画面側の仕事は「置く」ことだけ。置き忘れ・キーの重複・部品の取り違えは
画面を開くまで気づけないため、ここでまとめて止める。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.projects.models import Project, ProjectMember

#: 画面名 → (URL 名, コーチマークのキー, その画面に出るツールチップの語)
SCREENS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # 「テストのカバー率」の説明は判定可能なときだけ描画される KPI に付くため、
    # 常時描画される語だけをここに置く。
    "品質": ("dashboard:quality", "quality", ("消化率", "品質ゲート", "未計測")),
    "不具合": ("projects:defect_list", "defects", ("重大度", "検出工程", "クローズ")),
    "課題": ("projects:issue_list", "issues", ("期限超過", "外部キー")),
    "リスク": ("dashboard:risk", "risk", ("顕在化", "スコア", "課題化")),
    "変更影響分析": ("dashboard:change", "change", ("影響範囲", "日程影響", "判断")),
    "AI介入提案": ("dashboard:intervention", "intervention", ("根拠", "信頼度", "修正して採用")),
}


class CoachmarkPlacementTests(TestCase):
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
        self.client.force_login(self.user)

    def _get(self, url_name: str):
        response = self.client.get(reverse(url_name))
        self.assertEqual(response.status_code, 200)
        return response

    def _assert_coachmark(self, screen: str) -> None:
        url_name, coach_key, labels = SCREENS[screen]
        response = self._get(url_name)

        self.assertContains(response, f'data-coach="{coach_key}"')
        self.assertContains(response, "この画面の見方")
        # 手順が 1 件も出ていないコーチマークは、置いただけで役に立たない。
        self.assertContains(response, '<ol class="coach-steps">')
        self.assertGreaterEqual(response.content.decode().count("<li>"), 2)

        for label in labels:
            # 説明の読み上げ名は共通部品側の仕様。本文まで載せる実装もあるため、
            # 語の部分までを見る（部品の実装差でこのテストが割れないようにする）。
            self.assertContains(response, f'aria-label="{label}の説明')

    def test_品質画面に見方とツールチップがある(self) -> None:
        self._assert_coachmark("品質")

    def test_不具合一覧に見方とツールチップがある(self) -> None:
        self._assert_coachmark("不具合")

    def test_課題一覧に見方とツールチップがある(self) -> None:
        self._assert_coachmark("課題")

    def test_リスク一覧に見方とツールチップがある(self) -> None:
        self._assert_coachmark("リスク")

    def test_変更影響分析に見方とツールチップがある(self) -> None:
        self._assert_coachmark("変更影響分析")

    def test_AI介入提案に見方とツールチップがある(self) -> None:
        self._assert_coachmark("AI介入提案")

    def test_コーチマークのキーは画面ごとに一意(self) -> None:
        """キーが重複すると、片方を閉じたときにもう片方も出なくなる。"""

        keys = [coach_key for _, coach_key, _ in SCREENS.values()]

        self.assertEqual(len(keys), len(set(keys)), f"重複したキー: {keys}")

    def test_AI介入提案は候補が確定情報でないことを画面で明示する(self) -> None:
        """AI の候補と人が確定した情報を混同させないための文言を守る。"""

        response = self._get("dashboard:intervention")

        self.assertContains(response, "AIが出した候補で、確定した指示ではありません")
