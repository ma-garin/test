"""管制配下 3 画面の「次の行動へ着地できるか」の回帰テスト（UXP-01 / UXP-04 / UXP-05）。

固定したい性質:

- ダッシュボードは最優先の 1 件を KPI より上に、対象名・理由・期限・遷移先つきで出す
- 重要アラートは行ごとに台帳の絞り込み URL へ着地する（読めるだけの行を作らない）
- 危険がないときは、空欄ではなく監視対象数を出す
- 予兆検知は「プレビュー / 保存して実行 / 見送り理由」を分け、実行直前に件数と取り消し可否を出す
- 進捗予測の候補は、タスク詳細へ着地し、同じ順の 1 行要約を持つ
- 候補が 0 件のとき「AI介入提案へ」を主操作にしない
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.dashboard import views
from apps.dashboard.models import Alert
from apps.dashboard.services.detection.findings import Skip, SkipReason
from apps.projects.models import Project, ProjectMember, WbsTask

TODAY = timezone.localdate()


class ControlScreenTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="uxp", name="UXPテナント")
        self.user = User.objects.create_user(
            username="uxp-user",
            email="uxp-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.project = Project.objects.create(
            tenant=self.tenant, code="uxp-project", name="UXP案件"
        )
        ProjectMember.objects.create(
            project=self.project, user=self.user, role=ProjectRole.OWNER
        )
        self.client.force_login(self.user)

    def _task(self, name: str, *, status: str, end_offset: int, progress: int = 0) -> WbsTask:
        return WbsTask.objects.create(
            project=self.project,
            wbs_code="1.1",
            name=name,
            status=status,
            planned_start=TODAY - timedelta(days=10),
            planned_end=TODAY + timedelta(days=end_offset),
            progress_percent=progress,
        )


class ControlDashboardTests(ControlScreenTestBase):
    """UXP-01: プロジェクトダッシュボード。"""

    def test_最優先の1件をKPIより上に対象名と期限つきで出す(self) -> None:
        task = self._task("承認待ちタスク", status=WbsTask.Status.BLOCKED, end_offset=3)

        response = self.client.get(reverse("dashboard:control"))
        body = response.content.decode()

        self.assertContains(response, "次にやること")
        self.assertContains(response, "承認待ちタスク")
        self.assertContains(response, "ブロック中で着手できません")
        self.assertContains(response, (TODAY + timedelta(days=3)).strftime("%Y-%m-%d"))
        self.assertContains(response, reverse("projects:task_detail", args=[task.pk]))
        # KPI より上に置く。下にあると、数字を読んでから行動を探すことになる。
        self.assertLess(body.index("次にやること"), body.index("全体進捗率"))

    def test_重要アラートは台帳の絞り込みURLへ着地する(self) -> None:
        Alert.objects.create(
            project=self.project,
            category=Alert.Category.SCHEDULE,
            severity=Alert.Severity.CRITICAL,
            title="進捗遅延の予兆",
            detected_at=timezone.now(),
        )

        response = self.client.get(reverse("dashboard:control"))

        self.assertContains(response, f'href="{reverse("dashboard:tasks")}?due=overdue"')
        self.assertContains(response, "期限超過タスクを見る")

    def test_危険がないときは監視対象数を出す(self) -> None:
        response = self.client.get(reverse("dashboard:control"))

        self.assertContains(response, "いま対応が必要な項目はありません")
        self.assertContains(response, "監視対象")
        self.assertContains(response, "7日以内に期限を迎えるタスク")

    def test_分類ごとの着地先は台帳の絞り込みURLになる(self) -> None:
        expected = {
            Alert.Category.QUALITY: reverse("projects:defect_list") + "?status=new",
            Alert.Category.CHANGE: reverse("dashboard:change") + "?status=pending_approval",
            Alert.Category.RESOURCE: reverse("dashboard:tasks") + "?status=blocked",
            Alert.Category.RISK: reverse("dashboard:risk"),
        }

        for category, url in expected.items():
            with self.subTest(category=category):
                self.assertEqual(views._ledger_link(category)[0], url)


class DetectionScreenTests(ControlScreenTestBase):
    """UXP-04: 予兆検知。"""

    def test_保存実行の直前に作成予定件数と取り消し可否を出す(self) -> None:
        response = self.client.get(reverse("dashboard:detection"))
        body = response.content.decode()

        for heading in ("実行前プレビュー", "保存して実行", "見送り理由"):
            self.assertContains(response, heading)

        for label in ("新規作成予定", "重複除外", "判定不能", "取り消し:", "対象:"):
            self.assertContains(response, label)

        # 件数と取り消し可否はボタンより前に読ませる。
        self.assertLess(body.index("新規作成予定"), body.index("保存して実行（アラート"))
        self.assertLess(body.index("取り消し:"), body.index("保存して実行（アラート"))

    def test_保存実行の主操作は画面に1つだけ(self) -> None:
        response = self.client.get(reverse("dashboard:detection"))
        body = response.content.decode()

        self.assertEqual(body.count('class="btn-b"'), 1)
        self.assertNotContains(response, "検知を実行してアラートを作成")

    def test_判定不能は必要なデータを一文で示す(self) -> None:
        skip = Skip(
            project=self.project,
            kind="defect_rate",
            reason=SkipReason.INSUFFICIENT_DATA,
            detail="観測数が足りません",
        )
        other = Skip(
            project=self.project,
            kind="defect_rate",
            reason=SkipReason.DUPLICATE,
            detail="未対応アラートあり",
        )

        self.assertIn("不具合", views._data_need(skip))
        self.assertEqual(views._data_need(other), "")

        response = self.client.get(reverse("dashboard:detection"))
        self.assertContains(response, "必要なデータ")


class ProgressScreenTests(ControlScreenTestBase):
    """UXP-05: 進捗予測・介入。"""

    def test_遅延見込みタスクは詳細へ着地し同じ順の1行要約を持つ(self) -> None:
        task = self._task("遅延タスク", status=WbsTask.Status.IN_PROGRESS, end_offset=-5)

        response = self.client.get(reverse("dashboard:progress"))
        body = response.content.decode()

        self.assertContains(response, reverse("projects:task_detail", args=[task.pk]))
        self.assertContains(response, "遅延理由 期限超過")
        self.assertContains(response, "次アクション 着手日を決めて担当に依頼する")
        head = "遅延理由 期限超過"
        summary = body[body.index(head) + len(head) :]
        self.assertLess(summary.index("担当"), summary.index("期限"))
        self.assertLess(summary.index("期限"), summary.index("次アクション"))

    def test_ブロック中タスクも同じ順の1行要約を持つ(self) -> None:
        task = self._task("止まっているタスク", status=WbsTask.Status.BLOCKED, end_offset=2)

        response = self.client.get(reverse("dashboard:progress"))

        self.assertContains(response, reverse("projects:task_detail", args=[task.pk]))
        self.assertContains(response, "遅延理由 ブロック中で着手できない")
        self.assertContains(response, "次アクション ボール保持 未設定 に解消を依頼する")

    def test_候補が0件ならAI介入提案へを主操作にしない(self) -> None:
        response = self.client.get(reverse("dashboard:progress"))

        self.assertContains(
            response, f'class="btn-out sm" href="{reverse("dashboard:intervention")}"'
        )

        self._task("止まっているタスク", status=WbsTask.Status.BLOCKED, end_offset=2)
        response = self.client.get(reverse("dashboard:progress"))

        self.assertContains(
            response, f'class="btn-b sm" href="{reverse("dashboard:intervention")}"'
        )
        self.assertContains(response, "候補 1件")
