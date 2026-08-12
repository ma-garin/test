"""一覧画面のページング。

打ち切り（先頭 N 件）だと、総件数と実際に見える行数が食い違い、
データが無いのか切られたのかを利用者が判別できない。ここでは
「2 ページ目に残りが出る」「絞り込み条件がページ送りで消えない」
「集計値がページで変わらない」の 3 点を画面ごとに固定する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.agents.models import AgentRun
from apps.core.pagination import PAGE_SIZE
from apps.pmo.models import Deliverable, PlanDraft
from apps.projects.models import Defect, Issue, Project

#: 1 ページ（50 件）を超え、2 ページ目に 10 件残る件数。
TOTAL = 60


class ListPaginationTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pager-user",
            email="pager-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.client.force_login(self.user)

    def _assert_two_pages(self, url_name: str, context_key: str | None = None) -> None:
        """1 ページ目は上限まで、2 ページ目に残りが出ることを確かめる。

        `context_key` は画面が行を渡している変数名。指定が無ければ
        ページ本体（`page.object_list`）を直接見る。
        """

        url = reverse(url_name)

        def rows(response):
            page = response.context["page"]

            return page.object_list if context_key is None else response.context[context_key]

        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(rows(first)), PAGE_SIZE)
        self.assertEqual(first.context["page"].paginator.count, TOTAL)
        self.assertTrue(first.context["page"].has_next)

        second = self.client.get(url, {"page": 2})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(rows(second)), TOTAL - PAGE_SIZE)
        self.assertEqual(second.context["page"].paginator.count, TOTAL)

    def test_課題一覧は2ページに分かれる(self):
        Issue.objects.bulk_create(
            Issue(project=self.project, title=f"課題{index}") for index in range(TOTAL)
        )

        self._assert_two_pages("projects:issue_list", "issues")

    def test_不具合一覧は2ページに分かれる(self):
        Defect.objects.bulk_create(
            Defect(project=self.project, title=f"不具合{index}") for index in range(TOTAL)
        )

        self._assert_two_pages("projects:defect_list", "defects")

    def test_案件一覧は2ページに分かれる(self):
        Project.objects.bulk_create(
            Project(tenant=self.tenant, code=f"c{index:03d}", name=f"案件{index}")
            for index in range(TOTAL - 1)  # setUp の 1 件と合わせて TOTAL 件
        )

        self._assert_two_pages("projects:list", "projects")

    def test_Agenticトレース一覧は2ページに分かれる(self):
        AgentRun.objects.bulk_create(
            AgentRun(
                tenant=self.tenant,
                area=AgentRun.Area.PMO_CONSULTATION,
                user_input=f"質問{index}",
            )
            for index in range(TOTAL)
        )

        self._assert_two_pages("agents:run_list", "runs")

    def test_計画ドラフト一覧は2ページに分かれる(self):
        self._create_drafts()

        self._assert_two_pages("pmo:planning", "drafts")

    def test_成果物支援一覧は2ページに分かれる(self):
        self._create_deliverables()

        self._assert_two_pages("pmo:deliverables")

    def _create_drafts(self) -> list[PlanDraft]:
        return PlanDraft.objects.bulk_create(
            PlanDraft(project=self.project, title=f"計画{index}") for index in range(TOTAL)
        )

    def _create_deliverables(self) -> list[Deliverable]:
        return Deliverable.objects.bulk_create(
            Deliverable(
                project=self.project,
                title=f"成果物{index}",
                kind=Deliverable.Kind.WEEKLY_REPORT,
                ai_generated_body="AIの下書き",
                body="AIの下書き",
            )
            for index in range(TOTAL)
        )

    # --- 集計と絞り込みの保持 ---

    def test_成果物支援の集計は全件から出す(self):
        """KPI がページ送りで変わらないこと。ページごとに数字が動けば壊れている。"""

        self._create_deliverables()
        url = reverse("pmo:deliverables")

        first = self.client.get(url).context["report"]
        second = self.client.get(url, {"page": 2}).context["report"]

        self.assertEqual(first.total, TOTAL)
        self.assertEqual(second.total, TOTAL)
        self.assertEqual(first.average_correction_percent, second.average_correction_percent)

    def test_報告承認の集計は全件から出す(self):
        self._create_deliverables()
        url = reverse("pmo:approvals")

        first = self.client.get(url).context["report"]
        second = self.client.get(url, {"page": 2}).context["report"]

        self.assertEqual(first.total, TOTAL)
        self.assertEqual(second.total, TOTAL)
        self.assertEqual(first.blocked_count, second.blocked_count)

    def test_絞り込み条件はページ送りで保たれる(self):
        """選択中の計画（?draft=）がページャのリンクから消えないこと。"""

        drafts = self._create_drafts()
        selected = PlanDraft.objects.get(title=drafts[0].title)

        response = self.client.get(reverse("pmo:planning"), {"draft": selected.pk, "page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_query"], f"draft={selected.pk}&")
        # ページャのリンクが `?draft=..&page=..` の形になっている（& は HTML エスケープされる）
        self.assertContains(response, f"?draft={selected.pk}&amp;page=1")
        # 2 ページ目でも選択中の計画は全件から解決される
        self.assertEqual(response.context["selected"].pk, selected.pk)

    def test_ページ番号が範囲外でも画面は落ちない(self):
        """URL を手で編集した程度で一覧が 404 になると、詳細からの戻り導線が壊れる。"""

        Issue.objects.bulk_create(
            Issue(project=self.project, title=f"課題{index}") for index in range(TOTAL)
        )
        url = reverse("projects:issue_list")

        self.assertEqual(self.client.get(url, {"page": "999"}).status_code, 200)
        self.assertEqual(self.client.get(url, {"page": "abc"}).status_code, 200)

    def test_標準一覧の表示件数を切り替えられる(self):
        """一覧の密度を変えても、ページ送りと選択状態が連動すること。"""

        Issue.objects.bulk_create(
            Issue(project=self.project, title=f"課題{index}") for index in range(TOTAL)
        )

        response = self.client.get(reverse("projects:issue_list"), {"per_page": 20})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].paginator.per_page, 20)
        self.assertEqual(len(response.context["issues"]), 20)
        self.assertContains(response, 'value="20" selected')
        self.assertContains(response, "先頭")
        self.assertContains(response, "末尾")
