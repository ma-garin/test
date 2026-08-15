"""課題・不具合台帳の絞り込み。

件数が増えると、絞り込みの無い一覧は「全部見る」以外の使い方ができない。
ここで固定するのは 3 点。

1. 条件が実際に効くこと（画面に出す条件が飾りになっていないこと）
2. 総件数が *絞り込み後の全件* であり、ページ表示と食い違わないこと
   … 「12件」と出ているのに 50 行出ている、あるいはその逆は、利用者が
   データの欠けに気づけない壊れ方をする
3. 不正な値でも 500 にせず「絞り込まない」へ倒すこと
   … URL を手で編集した程度で画面が落ちると、一覧へ戻る導線ごと壊れる
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.pagination import PAGE_SIZE
from apps.projects.models import Defect, Issue, Project, Severity

#: 2 ページ目が必ず出る件数。
TOTAL = 60

#: 絞り込んでも 2 ページに跨る件数。総件数とページ表示の整合を見るために使う。
OWNED = 55


class IssueFilterTests(TestCase):
    def setUp(self) -> None:
        self.today = timezone.localdate()
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="issue-filter",
            email="issue-filter@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.client.force_login(self.user)
        self.url = reverse("projects:issue_list")

        self.overdue = Issue.objects.create(
            project=self.project,
            title="期限を過ぎた課題",
            status=Issue.Status.OPEN,
            severity=Severity.CRITICAL,
            owner="佐藤",
            due_date=self.today - timedelta(days=3),
        )
        self.soon = Issue.objects.create(
            project=self.project,
            title="期限が近い課題",
            status=Issue.Status.IN_PROGRESS,
            severity=Severity.LOW,
            owner="鈴木",
            due_date=self.today + timedelta(days=2),
        )
        self.no_due = Issue.objects.create(
            project=self.project,
            title="期限のない課題",
            status=Issue.Status.OPEN,
            severity=Severity.MEDIUM,
            owner="佐藤(PMO)",
        )
        # 解決済みで期限を過ぎたもの。期限超過に数えてはいけない。
        self.resolved = Issue.objects.create(
            project=self.project,
            title="解決済みの課題",
            status=Issue.Status.RESOLVED,
            severity=Severity.HIGH,
            owner="鈴木",
            due_date=self.today - timedelta(days=10),
        )

    def _titles(self, params: dict) -> list[str]:
        response = self.client.get(self.url, params)

        self.assertEqual(response.status_code, 200)

        return [issue.title for issue in response.context["issues"]]

    def test_状態で絞り込める(self):
        titles = self._titles({"status": Issue.Status.OPEN})

        self.assertCountEqual(titles, [self.overdue.title, self.no_due.title])

    def test_重大度で絞り込める(self):
        self.assertEqual(self._titles({"severity": Severity.CRITICAL}), [self.overdue.title])

    def test_担当は部分一致で絞り込める(self):
        """台帳の担当欄は自由入力で表記が揺れる。完全一致では現場に追従できない。"""

        titles = self._titles({"owner": "佐藤"})

        self.assertCountEqual(titles, [self.overdue.title, self.no_due.title])

    def test_期限超過で絞り込むと解決済みは含めない(self):
        self.assertEqual(self._titles({"due": "overdue"}), [self.overdue.title])

    def test_期限接近と期限未設定で絞り込める(self):
        self.assertEqual(self._titles({"due": "due_soon"}), [self.soon.title])
        self.assertEqual(self._titles({"due": "none"}), [self.no_due.title])

    def test_条件は組み合わせて効く(self):
        titles = self._titles({"status": Issue.Status.OPEN, "owner": "佐藤", "due": "overdue"})

        self.assertEqual(titles, [self.overdue.title])

    def test_不正な値では絞り込まない(self):
        """500 にせず、かつ 0 件にもしない。壊れているのか該当が無いのか判別できなくなる。"""

        for params in ({"status": "存在しない状態"}, {"severity": "9"}, {"due": "yesterday"}):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["total"], Issue.objects.count())

    def test_総件数は絞り込み後の全件でページ表示と食い違わない(self):
        Issue.objects.all().delete()
        Issue.objects.bulk_create(
            Issue(
                project=self.project,
                title=f"課題{index}",
                owner="佐藤" if index < OWNED else "鈴木",
            )
            for index in range(TOTAL)
        )

        first = self.client.get(self.url, {"owner": "佐藤"})
        second = self.client.get(self.url, {"owner": "佐藤", "page": 2})

        # 総件数は絞り込み後の全件。ページを送っても動かない。
        self.assertEqual(first.context["total"], OWNED)
        self.assertEqual(second.context["total"], OWNED)
        self.assertEqual(first.context["page"].paginator.count, OWNED)
        self.assertEqual(len(first.context["issues"]), PAGE_SIZE)
        self.assertEqual(len(second.context["issues"]), OWNED - PAGE_SIZE)

    def test_ページ送りで絞り込み条件が消えない(self):
        response = self.client.get(self.url, {"owner": "佐藤", "status": Issue.Status.OPEN})

        self.assertIn("owner=", response.context["page_query"])
        self.assertIn("status=open", response.context["page_query"])
        self.assertNotIn("page=", response.context["page_query"])

    def test_絞り込み条件は画面の選択状態に残る(self):
        response = self.client.get(self.url, {"status": Issue.Status.OPEN, "due": "overdue"})
        filters = response.context["filters"]

        self.assertEqual(filters.status, Issue.Status.OPEN)
        self.assertEqual(filters.due, "overdue")
        self.assertTrue(filters.is_active)

    def test_他テナントの課題は絞り込みでも出てこない(self):
        other = Tenant.objects.create(code="globex", name="Globex")
        foreign_project = Project.objects.create(tenant=other, code="gx", name="他社案件")
        Issue.objects.create(
            project=foreign_project, title="他テナントの課題", owner="佐藤", status=Issue.Status.OPEN
        )

        self.assertNotIn("他テナントの課題", self._titles({"owner": "佐藤"}))


class DefectFilterTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="defect-filter",
            email="defect-filter@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.client.force_login(self.user)
        self.url = reverse("projects:defect_list")

        self.critical = Defect.objects.create(
            project=self.project,
            title="決済が二重に走る",
            status=Defect.Status.NEW,
            severity=Severity.CRITICAL,
            phase="結合テスト",
        )
        self.closed = Defect.objects.create(
            project=self.project,
            title="表示崩れ",
            status=Defect.Status.CLOSED,
            severity=Severity.LOW,
            phase="単体テスト",
        )

    def _titles(self, params: dict) -> list[str]:
        response = self.client.get(self.url, params)

        self.assertEqual(response.status_code, 200)

        return [defect.title for defect in response.context["defects"]]

    def test_状態で絞り込める(self):
        self.assertEqual(self._titles({"status": Defect.Status.NEW}), [self.critical.title])

    def test_重大度で絞り込める(self):
        self.assertEqual(self._titles({"severity": Severity.LOW}), [self.closed.title])

    def test_検出工程は部分一致で絞り込める(self):
        """工程名は自由入力。「結合」で「結合テスト」を引けないと使えない。"""

        self.assertEqual(self._titles({"phase": "結合"}), [self.critical.title])

    def test_不正な値では絞り込まない(self):
        for params in ({"status": "unknown"}, {"severity": "とても高い"}):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["total"], Defect.objects.count())

    def test_総件数は絞り込み後の全件でページ表示と食い違わない(self):
        Defect.objects.all().delete()
        Defect.objects.bulk_create(
            Defect(
                project=self.project,
                title=f"不具合{index}",
                phase="結合テスト" if index < OWNED else "総合テスト",
            )
            for index in range(TOTAL)
        )

        first = self.client.get(self.url, {"phase": "結合"})
        second = self.client.get(self.url, {"phase": "結合", "page": 2})

        self.assertEqual(first.context["total"], OWNED)
        self.assertEqual(second.context["total"], OWNED)
        self.assertEqual(len(first.context["defects"]), PAGE_SIZE)
        self.assertEqual(len(second.context["defects"]), OWNED - PAGE_SIZE)

    def test_ページ送りで絞り込み条件が消えない(self):
        response = self.client.get(self.url, {"phase": "結合", "severity": Severity.CRITICAL})

        self.assertIn("phase=", response.context["page_query"])
        self.assertIn("severity=critical", response.context["page_query"])
