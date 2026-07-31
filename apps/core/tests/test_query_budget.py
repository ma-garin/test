"""クエリ本数と件数スケールの検証。

これまで性能をまったく測っていなかった。ページングは入れたが、
**1 行ごとに追加クエリが飛んでいれば、ページを切っても遅い**。

ここでやるのは 2 つ。

1. **クエリ本数に上限を置く**（`assertNumQueries`）。行数を増やしても本数が
   変わらないことを確かめる。増えるなら N+1 が入った合図
2. **実データ規模で開く**。1 案件 1,000 タスクで一覧・ガント・検知が
   現実的な本数で返ること

上限値は「現状 + 余裕」ではなく**現状の実測値**を置く。余裕を持たせると、
N+1 が 1 本ずつ増えても気づけない。増えたら落ちて、意図的なら数値を更新する。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.dashboard.models import Alert
from apps.projects.models import (
    Defect,
    Issue,
    Priority,
    Project,
    ProjectMember,
    Risk,
    Severity,
    WbsTask,
)

TODAY = timezone.localdate()

#: 行数を増やしても本数が変わらないことを確かめるための 2 水準。
SMALL_ROWS = 5
LARGE_ROWS = 200

#: 実データ規模の目安。1 案件あたりのタスク数。
SCALE_ROWS = 1_000


class QueryBudgetBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(
            tenant=self.tenant, code="p1", name="基幹刷新", progress_percent=50
        )
        ProjectMember.objects.create(
            project=self.project, user=self.user, role=ProjectRole.OWNER
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["current_tenant_id"] = str(self.tenant.pk)
        session.save()

    def make_tasks(self, count: int) -> None:
        # 2 回目以降の呼び出しで WBS 番号が衝突しないよう、既存件数から続ける。
        start = WbsTask.objects.filter(project=self.project).count()
        WbsTask.objects.bulk_create(
            WbsTask(
                project=self.project,
                wbs_code=f"{index // 10}.{index % 10}-{index}",
                name=f"タスク{index}",
                owner=f"担当{index % 7}",
                status=WbsTask.Status.IN_PROGRESS,
                priority=Priority.MEDIUM,
                planned_start=TODAY - timedelta(days=20),
                planned_end=TODAY + timedelta(days=index % 30 - 10),
                progress_percent=index % 100,
                is_critical_path=index % 11 == 0,
            )
            for index in range(start, start + count)
        )

    def make_issues(self, count: int) -> None:
        start = Issue.objects.filter(project=self.project).count()
        Issue.objects.bulk_create(
            Issue(
                project=self.project,
                title=f"課題{index}",
                status=Issue.Status.OPEN,
                severity=Severity.HIGH if index % 3 == 0 else Severity.MEDIUM,
                due_date=TODAY + timedelta(days=index % 10 - 5),
            )
            for index in range(start, start + count)
        )

    def count_queries(self, url: str, params: dict | None = None) -> int:
        """画面 1 回分のクエリ本数を数える。"""

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url, params or {})
            self.assertEqual(response.status_code, 200)

        return len(captured)


class QueryCountStabilityTests(QueryBudgetBase):
    """行数を増やしてもクエリ本数が変わらないこと（N+1 の検出）。"""

    def assertStable(self, url: str, params: dict | None = None) -> None:
        self.make_tasks(SMALL_ROWS)
        self.make_issues(SMALL_ROWS)
        small = self.count_queries(url, params)

        self.make_tasks(LARGE_ROWS)
        self.make_issues(LARGE_ROWS)
        large = self.count_queries(url, params)

        self.assertEqual(
            small,
            large,
            f"{url}: 行数を {SMALL_ROWS} → {LARGE_ROWS + SMALL_ROWS} にしたら "
            f"クエリが {small} → {large} 本に増えた（N+1 の疑い）",
        )

    def test_タスク一覧のクエリ本数は行数で変わらない(self) -> None:
        self.assertStable(reverse("dashboard:tasks"))

    def test_課題一覧のクエリ本数は行数で変わらない(self) -> None:
        self.assertStable(reverse("projects:issue_list"))

    def test_管制ダッシュボードのクエリ本数は行数で変わらない(self) -> None:
        self.assertStable(reverse("dashboard:control"))

    def test_進捗予測のクエリ本数は行数で変わらない(self) -> None:
        self.assertStable(reverse("dashboard:progress"))

    def test_ガント表示のクエリ本数は行数で変わらない(self) -> None:
        self.assertStable(reverse("dashboard:tasks"), {"view": "gantt"})

    def test_予兆検知のクエリ本数は行数で変わらない(self) -> None:
        self.assertStable(reverse("dashboard:detection"))

    def test_案件一覧のクエリ本数は行数で変わらない(self) -> None:
        self.assertStable(reverse("projects:list"))


class QueryBudgetTests(QueryBudgetBase):
    """主要画面のクエリ本数に上限を置く。

    上限は実測値。増えたら落ちるので、意図的な変更なら数値を更新する。
    """

    #: 画面 → 許容するクエリ本数。実測 + 0 で置く。
    BUDGET = {
        "dashboard:tasks": 26,
        "projects:issue_list": 18,
        "dashboard:control": 26,
        "projects:list": 16,
    }

    def test_主要画面が想定本数以内で描画される(self) -> None:
        self.make_tasks(50)
        self.make_issues(50)

        for url_name, budget in self.BUDGET.items():
            with self.subTest(screen=url_name):
                actual = self.count_queries(reverse(url_name))

                self.assertLessEqual(
                    actual,
                    budget,
                    f"{url_name}: クエリが {actual} 本（上限 {budget} 本）。"
                    "意図した変更なら BUDGET を更新すること",
                )


class ScaleTests(QueryBudgetBase):
    """実データ規模で開けること。"""

    def test_タスク1000件でも一覧が開く(self) -> None:
        self.make_tasks(SCALE_ROWS)

        response = self.client.get(reverse("dashboard:tasks"))

        self.assertEqual(response.status_code, 200)
        # ページングが効いていること。全件描画すると画面が実用に耐えない。
        self.assertLessEqual(len(response.context["board"].rows), 50)
        self.assertEqual(response.context["page"].paginator.count, SCALE_ROWS)

    def test_タスク1000件でもガントが開く(self) -> None:
        self.make_tasks(SCALE_ROWS)

        response = self.client.get(reverse("dashboard:tasks"), {"view": "gantt"})

        self.assertEqual(response.status_code, 200)

    def test_タスク1000件でも入力ルールの集計が返る(self) -> None:
        """入力ルールは絞り込みに関係なく全件を見る。ここが重い経路になりやすい。"""

        self.make_tasks(SCALE_ROWS)

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("dashboard:tasks"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["input_rules"].task_total, SCALE_ROWS)
        # 1,000 件を 1 度読むだけで、1 件ずつ引かないこと。
        self.assertLess(len(captured), 60, "1,000 件で 60 本を超えている")

    def test_課題1000件でも一覧が開く(self) -> None:
        self.make_issues(SCALE_ROWS)

        response = self.client.get(reverse("projects:issue_list"))

        self.assertEqual(response.status_code, 200)

    def test_検知が1000件のタスクで完了する(self) -> None:
        self.make_tasks(SCALE_ROWS)
        Alert.objects.create(
            project=self.project,
            title="既存アラート",
            category=Alert.Category.SCHEDULE,
            severity=Alert.Severity.WARNING,
            detected_at=timezone.now(),
        )
        Defect.objects.create(
            project=self.project,
            title="不具合",
            severity=Severity.CRITICAL,
            detected_on=TODAY - timedelta(days=1),
        )
        Risk.objects.create(project=self.project, title="リスク", probability=4, impact=4)

        response = self.client.get(reverse("dashboard:detection"))

        self.assertEqual(response.status_code, 200)
