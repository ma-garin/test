"""実ブラウザによる業務フローの検証（E2E）。

`test_screen_scenarios.py` は HTTP レイヤまでしか見ていない。200 が返っても、
JavaScript が動かずサイドバーが開かない、ボタンが要素の下に隠れて押せない、
という壊れ方は捕まえられない。ここは実際に Chromium を起動して**押す**。

検証するのは「利用者が最初から最後までやり切れるか」。
画面単位ではなく、**業務の流れ**を 1 本ずつ通す。

- 落ちてよいのは「意図した拒否（403 の画面）」だけ
- JS エラーはすべて失敗として扱う（`console.error` を拾う）
- 待ち時間は固定 sleep を使わない。要素の出現を待つ

実行が重いので、ブラウザは全テストで 1 つを使い回す（`setUpClass`）。
"""

from __future__ import annotations

import os
import unittest
from datetime import timedelta

# playwright の同期 API はイベントループ上で動くため、Django が「非同期文脈から
# 同期 ORM を呼んだ」と判断してしまう。テストプロセスは実際には単一スレッドで
# 直列に動いているので、ここだけ明示的に許可する（Django 公式の案内どおり）。
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import tag
from django.utils import timezone

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.dashboard.models import Alert, InterventionProposal
from apps.projects.models import (
    ChangeRequest,
    Defect,
    Issue,
    Milestone,
    Priority,
    Project,
    ProjectMember,
    QualityMetric,
    Risk,
    Severity,
    WbsTask,
)

#: 環境が用意している Chromium。playwright 側のバージョン固定と食い違うため明示する。
#: 既定のパスが 1 つだけだと、そこに無い開発機では E2E が丸ごと動かないまま
#: 「テストは通っている」状態になる（実際にこの取り違えが起きた）。候補を順に探す。
CHROMIUM_CANDIDATES = (
    os.environ.get("E2E_CHROMIUM", ""),
    "/opt/pw-browsers/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
)


def _find_chromium() -> str:
    for path in CHROMIUM_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return ""


CHROMIUM_PATH = _find_chromium()

#: 要素の出現待ち（ミリ秒）。固定 sleep は使わない。
TIMEOUT_MS = 10_000

TODAY = timezone.localdate()


@tag("e2e")
class BrowserFlowTests(StaticLiveServerTestCase):
    """ブラウザで業務フローを通す。

    `StaticLiveServerTestCase` を使うのは CSS/JS を実際に配信するため。
    `LiveServerTestCase` だと静的ファイルが 404 になり、
    「JS が動かない」のが不具合なのか配信漏れなのか切り分けられない。
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()

        # 足りないものを名指しで飛ばす。ImportError や起動失敗のまま落とすと、
        # 「E2E が 1 件も動いていない」ことに気づかず完了扱いにしてしまう。
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragma: no cover - 環境依存
            raise unittest.SkipTest(
                "playwright 未導入。`pip install -r requirements/dev.txt` を実行してください。"
            ) from None

        if not CHROMIUM_PATH:
            raise unittest.SkipTest(
                "Chromium が見つかりません。E2E_CHROMIUM に実行ファイルのパスを指定してください。"
            )

        cls._playwright = sync_playwright().start()
        cls.browser = cls._playwright.chromium.launch(executable_path=CHROMIUM_PATH)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls._playwright.stop()
        super().tearDownClass()

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
            display_name="PMO 太郎",
        )
        self.project = Project.objects.create(
            tenant=self.tenant,
            code="p1",
            name="基幹刷新",
            progress_percent=50,
            planned_start=TODAY - timedelta(days=30),
            planned_end=TODAY + timedelta(days=30),
        )
        ProjectMember.objects.create(
            project=self.project, user=self.user, role=ProjectRole.OWNER
        )

        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.page = self.context.new_page()
        self.console_errors: list[str] = []
        self.page.on(
            "console",
            lambda message: self.console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        self.page.on("pageerror", lambda error: self.console_errors.append(str(error)))
        # コンソールの "Failed to load resource" だけでは、何が 404 なのか分からない。
        # URL を控えて、失敗メッセージから追えるようにする。
        self.failed_requests: list[str] = []
        self.page.on(
            "response",
            lambda response: self.failed_requests.append(
                f"{response.status} {response.url}"
            )
            if response.status >= 400
            else None,
        )
        self.page.on(
            "requestfailed",
            lambda request: self.failed_requests.append(f"failed {request.url}"),
        )
        self.page.on("requestfinished", lambda request: self.all_requests.append(request.url))
        self.all_requests: list[str] = []

    def tearDown(self) -> None:
        # JS エラーは「画面は出たが操作できない」形で表面化する。全フローで見る。
        self.assertEqual(
            self.console_errors,
            [],
            "ブラウザのコンソールにエラーが出た"
            f"（失敗した通信: {self.failed_requests}）",
        )
        self.context.close()

    # --- 補助 -----------------------------------------------------------

    def go(self, path: str) -> None:
        self.page.goto(f"{self.live_server_url}{path}", timeout=TIMEOUT_MS)

    def login(self) -> None:
        """メールアドレスのみのログイン（仕様どおりパスワードは無い）。"""

        self.go("/accounts/login/")
        self.page.fill("input[name='email']", self.user.email)
        # ログイン画面は認証用レイアウトで、ヘッダもサイドバーも無い。
        self.page.click("form.form button[type='submit']")
        self.page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)

    def click_and_wait(self, selector: str) -> None:
        self.page.click(selector, timeout=TIMEOUT_MS)
        self.page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)

    def submit(self, selector: str = "button[type='submit']", *, confirm: bool = False) -> None:
        """本文領域の送信ボタンを押す。

        ヘッダにもログアウトの `button[type=submit]` があり、DOM 上はそちらが先。
        範囲を `main` に限定しないと、保存したつもりでログアウトしている。
        """

        if confirm:
            self.page.once("dialog", lambda dialog: dialog.accept())
        self.click_and_wait(f"main {selector}")

    def body_text(self) -> str:
        return self.page.inner_text("body")

    # --- フロー ---------------------------------------------------------

    def test_フロー01_ログインして管制ダッシュボードに着く(self) -> None:
        self.login()

        self.assertIn("/", self.page.url)
        self.assertIn("プロジェクトダッシュボード", self.body_text())
        self.assertIn("基幹刷新", self.body_text())

    def test_フロー02_サイドバーを操作して目的の画面へ移動できる(self) -> None:
        """JS の折りたたみが壊れていると、ここで初めて分かる。"""

        self.login()

        # 「その他」に畳んだ画面へ、開いてから到達できること。
        self.page.click("text=タスク一覧", timeout=TIMEOUT_MS)
        self.page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
        self.assertIn("タスク一覧", self.body_text())

        # 子メニューを畳んでも、親カテゴリのレールは残り、再度開けること。
        self.page.click("#nav-collapse", timeout=TIMEOUT_MS)
        self.page.wait_for_timeout(200)
        self.assertTrue(self.page.is_visible("#nav-rail-toggle"))
        self.page.click("#nav-rail-toggle", timeout=TIMEOUT_MS)
        self.assertTrue(self.page.is_visible(".sb-item"), "子メニューを再表示できない")

    def test_フロー03_タスクを作って編集してアーカイブする(self) -> None:
        self.login()
        self.go("/projects/tasks/new/")

        self.page.select_option("select[name='project']", str(self.project.pk))
        self.page.fill("input[name='wbs_code']", "1.1")
        self.page.fill("input[name='name']", "要件定義レビュー")
        self.page.fill("input[name='owner']", "田中")
        self.page.fill("input[name='planned_end']", str(TODAY + timedelta(days=5)))
        self.submit()

        self.assertIn("要件定義レビュー", self.body_text())
        task = WbsTask.objects.get(wbs_code="1.1")

        # 編集して、一覧へ反映されること。
        self.go(f"/projects/tasks/{task.pk}/edit/")
        self.page.fill("input[name='name']", "要件定義レビュー（改）")
        self.submit()

        self.assertIn("要件定義レビュー（改）", self.body_text())

        # アーカイブすると一覧から消えること。
        self.go(f"/projects/tasks/{task.pk}/edit/")
        self.submit("form[action*='archive'] button", confirm=True)

        task.refresh_from_db()
        self.assertEqual(task.status, WbsTask.Status.ARCHIVED)
        # 判定は一覧表の中身で行う。完了メッセージにはタスク名が載るため、
        # 画面全体で見ると「消えたのに残っている」ように読めてしまう。
        self.assertNotIn(
            "要件定義レビュー（改）", self.page.inner_text(".dashboard-risk-table")
        )

    def test_フロー04_ガント表示へ切り替えてもタスクが見える(self) -> None:
        WbsTask.objects.create(
            project=self.project,
            wbs_code="2.1",
            name="結合試験",
            owner="鈴木",
            priority=Priority.HIGH,
            planned_start=TODAY - timedelta(days=5),
            planned_end=TODAY + timedelta(days=5),
            progress_percent=30,
        )
        self.login()
        self.go("/tasks/")
        self.click_and_wait("a[href*='view=gantt']")

        self.assertIn("結合試験", self.body_text())
        self.assertTrue(
            self.page.is_visible(".gantt-bar, .gt-bar, [class*='gantt']"),
            "ガントのバーが描画されていない",
        )

    def test_フロー05_課題を起票してクローズする(self) -> None:
        self.login()
        self.go("/projects/issues/new/")

        self.page.select_option("select[name='project']", str(self.project.pk))
        self.page.fill("input[name='title']", "テスト環境が確保できない")
        self.page.select_option("select[name='severity']", Severity.HIGH)
        self.submit()

        self.assertIn("テスト環境が確保できない", self.body_text())

        issue = Issue.objects.get(title="テスト環境が確保できない")
        self.go(f"/projects/issues/{issue.pk}/edit/")
        self.submit("form[action*='close'] button", confirm=True)

        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.Status.CLOSED)

    def test_フロー06_リスクを課題へ転換する(self) -> None:
        risk = Risk.objects.create(
            project=self.project,
            title="要員が確保できない",
            probability=4,
            impact=5,
            due_date=TODAY + timedelta(days=7),
        )
        self.login()
        self.go(f"/projects/risks/{risk.pk}/promote/")

        self.page.fill("input[name='title']", "要員不足が顕在化した")
        self.submit()

        risk.refresh_from_db()
        self.assertEqual(risk.status, Risk.Status.MATERIALIZED)
        self.assertTrue(Issue.objects.filter(title="要員不足が顕在化した").exists())

    def test_フロー07_成果物を生成し編集して事実チェックを見る(self) -> None:
        self._seed_report_material()
        self.login()
        self.go("/pmo/deliverables/")

        self.page.select_option("select[name='project']", str(self.project.pk))
        self.page.select_option("select[name='generator']", "weekly_report")
        self.submit("form.form button[type='submit']")

        body = self.body_text()
        self.assertIn("週次報告", body)
        self.assertIn("事実チェック", body)
        self.assertIn("一致", body)

    def test_フロー08_変更要求を判断して証跡が残る(self) -> None:
        change = ChangeRequest.objects.create(
            project=self.project,
            title="帳票レイアウトの変更",
            status=ChangeRequest.Status.PENDING_APPROVAL,
        )
        self.login()
        self.go(f"/projects/changes/{change.pk}/decide/")

        self.page.check("input[name='decision'][value='approved']")
        self.page.fill("textarea[name='reason']", "影響範囲が限定的なため承認する")
        self.submit(confirm=True)

        change.refresh_from_db()
        self.assertEqual(change.status, ChangeRequest.Status.APPROVED)
        self.assertEqual(change.decided_by, self.user)

    def test_フロー09_AI介入提案を判断する(self) -> None:
        alert = Alert.objects.create(
            project=self.project,
            title="クリティカルパス遅延",
            category=Alert.Category.SCHEDULE,
            severity=Alert.Severity.CRITICAL,
            detected_at=timezone.now(),
        )
        proposal = InterventionProposal.objects.create(
            project=self.project,
            alert=alert,
            title="要員を追加する",
            rationale="後続3件が停止している",
            recommended_action="1名追加",
            confidence=0.8,
        )
        self.login()
        self.go(f"/intervention/{proposal.pk}/decide/")

        self.page.select_option("select[name='status']", InterventionProposal.Status.ACCEPTED)
        self.page.fill("textarea[name='decision_reason']", "根拠が妥当なため採用する")
        self.submit(confirm=True)

        proposal.refresh_from_db()
        self.assertEqual(proposal.status, InterventionProposal.Status.ACCEPTED)
        self.assertEqual(proposal.decided_by, self.user)

    def test_フロー10_案件を切り替えると全画面に効く(self) -> None:
        other = Project.objects.create(tenant=self.tenant, code="p2", name="ECサイト刷新")
        ProjectMember.objects.create(
            project=other, user=self.user, role=ProjectRole.OWNER
        )
        WbsTask.objects.create(
            project=self.project, wbs_code="1.1", name="基幹側のタスク", priority=Priority.MEDIUM
        )
        WbsTask.objects.create(
            project=other, wbs_code="1.1", name="EC側のタスク", priority=Priority.MEDIUM
        )

        self.login()
        self.go("/accounts/project/")
        self.page.check(f"input[name='project'][value='{other.pk}']")
        self.submit()

        self.go("/tasks/")
        body = self.body_text()
        self.assertIn("EC側のタスク", body)
        self.assertNotIn("基幹側のタスク", body)

    def test_フロー11_PMO相談で直前の画面が文脈として出る(self) -> None:
        self.login()
        self.go("/tasks/")
        self.go("/pmo/consultation/")

        body = self.body_text()
        self.assertIn("直前に開いていた画面", body)
        self.assertIn("タスク一覧", body)

    def test_フロー12_RAG検索で引用元とスコア内訳が出る(self) -> None:
        """検索結果に根拠が付いていること。ここが無いと結果を信用できない。"""

        self._build_index()
        self.login()
        self.go("/rag/search/")

        self.page.fill("input[name='q']", "結合試験の完了判定")
        self.submit()

        body = self.body_text()
        self.assertIn("標準プロセス", body)
        self.assertIn("検索結果", body)
        # スコア内訳（統合・ベクトル・語彙）が出ていること。
        self.assertIn("統合", body)

    def test_フロー13_索引が未構築なら手順が案内される(self) -> None:
        """導入直後に「壊れている」と誤解されないこと。"""

        self.login()
        self.go("/rag/search/")

        self.page.fill("input[name='q']", "結合試験")
        self.submit()

        body = self.body_text()
        self.assertIn("検索インデックスが未構築です", body)
        self.assertIn("rebuild_index", body)

    # --- 材料 -----------------------------------------------------------

    def _build_index(self) -> None:
        """検索できる状態を作る。文書 1 件を登録して索引を張る。"""

        from apps.documents.models import Document, DocumentPage, FileType
        from apps.rag.models import IndexScope, VectorIndex
        from apps.rag.services.indexer import rebuild_index

        document = Document.objects.create(
            tenant=self.tenant,
            title="標準プロセス",
            file="documents/standard.txt",
            file_type=FileType.TXT,
        )
        DocumentPage.objects.create(
            document=document, page_number=1, content="結合試験の完了判定について記載する。"
        )
        index, _ = VectorIndex.objects.get_or_create(
            tenant=self.tenant, project=None, defaults={"scope": IndexScope.TENANT}
        )
        rebuild_index(index)

    def _seed_report_material(self) -> None:
        for index in range(3):
            WbsTask.objects.create(
                project=self.project,
                wbs_code=f"1.{index}",
                name=f"タスク{index}",
                owner="担当",
                status=WbsTask.Status.DONE if index == 0 else WbsTask.Status.IN_PROGRESS,
                priority=Priority.MEDIUM,
                planned_start=TODAY - timedelta(days=10),
                planned_end=TODAY + timedelta(days=5),
                progress_percent=100 if index == 0 else 40,
            )

        Issue.objects.create(
            project=self.project,
            title="未解決の課題",
            severity=Severity.HIGH,
            due_date=TODAY + timedelta(days=3),
        )
        Defect.objects.create(
            project=self.project,
            title="集計値が合わない",
            severity=Severity.CRITICAL,
            phase="結合試験",
            detected_on=TODAY - timedelta(days=2),
        )
        QualityMetric.objects.create(
            project=self.project,
            measured_on=TODAY,
            metric_key="test_pass_rate",
            metric_label="テスト消化率",
            value=80,
            target_value=100,
            unit="%",
        )
        Milestone.objects.create(
            project=self.project,
            name="結合試験完了",
            planned_date=TODAY + timedelta(days=10),
            is_gate=True,
        )
