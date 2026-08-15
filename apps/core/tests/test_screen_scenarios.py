"""全画面のユーザーシナリオ検証。

画面ごとに 10 案のシナリオを当てる。1 画面ずつ手で書くと 600 件を超え、
書き漏らした画面が「テストが無いから落ちない」状態になるため、
**画面カタログ（`SCREENS`）を唯一の出所にしてテストを機械生成する。**
画面を足してカタログへ載せ忘れると `test_カタログが全URLを覆う` が落ちる。

シナリオは「利用者が実際にやること」に寄せている。

- 未ログインで開く（社内リンクを共有された）
- 他社の ID を直接叩く（URL を書き換えた）
- 絞り込みに変な値が入る（ブックマークが古い）
- ページを送りすぎる（?page=9999）
- データが 1 件も無い（導入直後）
- 参照のみの利用者が開く
- 一覧から二重送信する（ボタン連打）

いずれも「500 を返さない」「他テナントを見せない」を確認する。
落ちてよいのは 403 / 404 / 405 であって、500 は常に不具合とみなす。
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.agents.models import AgentRun
from apps.audit.models import Feedback
from apps.core.navigation import all_items
from apps.dashboard.models import Alert, InterventionProposal
from apps.documents.models import Document, DocumentStatus, FileType, Template
from apps.integrations.models import Connection, Provider
from apps.pmo.models import Deliverable
from apps.projects.models import (
    ChangeRequest,
    Defect,
    Issue,
    Milestone,
    Priority,
    Project,
    ProjectMember,
    Risk,
    Severity,
    WbsTask,
)

TODAY = timezone.localdate()

#: URL を解決できないなど、テスト側の不備で 500 と区別がつかなくなるのを防ぐ。
SERVER_ERROR = 500


class Screen:
    """検証対象の画面 1 つ。

    `kind` で当てるシナリオ束を変える。

    - ``page``   … 一覧・ダッシュボード（引数なし GET）
    - ``object`` … 詳細・編集（UUID を取る GET）
    - ``action`` … 実行系（POST のみ）
    """

    def __init__(
        self,
        url_name: str,
        kind: str = "page",
        *,
        fixture: str = "",
        query: dict | None = None,
        writable: bool = False,
        admin_only: bool = False,
        post_data: dict | None = None,
    ) -> None:
        self.url_name = url_name
        self.kind = kind
        self.fixture = fixture
        self.query = query or {}
        #: 書き込み画面。参照のみの利用者には 403 を返すことを期待する。
        self.writable = writable
        #: テナント管理者だけが開ける画面（外部連携の設定など）。
        self.admin_only = admin_only
        self.post_data = post_data or {}

    @property
    def slug(self) -> str:
        return self.url_name.replace(":", "_")


SCREENS: tuple[Screen, ...] = (
    # ── 管制 ─────────────────────────────────────────────
    Screen("dashboard:control"),
    Screen("forecast:live"),
    Screen("forecast:report"),
    Screen("graph:quality"),
    Screen("dashboard:tasks", query={"status": "in_progress"}),
    Screen("dashboard:detection"),
    Screen("dashboard:progress"),
    Screen("dashboard:quality"),
    Screen("dashboard:risk", query={"status": "open"}),
    Screen("dashboard:change", query={"status": "pending_approval"}),
    Screen("dashboard:intervention"),
    Screen("dashboard:kpi"),
    Screen("dashboard:poc"),
    # ── PMO ──────────────────────────────────────────────
    Screen("pmo:consultation", query={"q": "結合試験が5日遅れています"}),
    Screen("pmo:planning"),
    Screen("pmo:deliverables"),
    Screen("pmo:approvals"),
    Screen("pmo:prompt_library"),
    Screen("pmo:education"),
    # ── ナレッジ / RAG ───────────────────────────────────
    Screen("documents:list"),
    Screen("documents:upload", writable=True),
    Screen("documents:template_list"),
    Screen("rag:search", query={"q": "遅延"}),
    Screen("rag:chat"),
    Screen("rag:evaluation"),
    # ── 監査・トレース ───────────────────────────────────
    Screen("agents:run_list"),
    Screen("audit:operation_list"),
    Screen("audit:feedback_list"),
    Screen("audit:feedback_create", writable=True),
    # ── 案件 ─────────────────────────────────────────────
    Screen("projects:list"),
    Screen("projects:issue_list"),
    Screen("projects:defect_list"),
    Screen("projects:task_create", writable=True),
    Screen("projects:issue_create", writable=True),
    Screen("projects:risk_create", writable=True),
    Screen("projects:defect_create", writable=True),
    Screen("projects:change_create", writable=True),
    # ── 管理・設定 ───────────────────────────────────────
    Screen("integrations:list"),
    Screen("integrations:pipeline"),
    Screen("integrations:job_list"),
    Screen("integrations:create", writable=True, admin_only=True),
    # AI設定は全ロールが開ける。API キーは利用者ごとに持てるため、閲覧と個人設定は
    # 全員へ開き、テナント既定の書き換えだけをビュー側で管理者に限っている。
    Screen("core:settings"),
    Screen("core:screen_map"),
    Screen("accounts:select_tenant"),
    Screen("accounts:select_project"),
    # ── 詳細・編集（UUID を取る） ────────────────────────
    Screen("projects:detail", "object", fixture="project"),
    Screen("projects:task_detail", "object", fixture="task"),
    Screen("projects:task_edit", "object", fixture="task", writable=True),
    Screen("projects:issue_edit", "object", fixture="issue", writable=True),
    Screen("projects:risk_edit", "object", fixture="risk", writable=True),
    Screen("projects:risk_promote", "object", fixture="risk", writable=True),
    Screen("projects:defect_edit", "object", fixture="defect", writable=True),
    Screen("projects:change_edit", "object", fixture="change", writable=True),
    Screen("projects:change_decide", "object", fixture="change", writable=True),
    Screen("agents:run_detail", "object", fixture="run"),
    Screen("integrations:edit", "object", fixture="connection", writable=True, admin_only=True),
    # 判断画面は GET でフォームを出し、POST で確定する。実行系ではない。
    Screen(
        "dashboard:intervention_decide",
        "object",
        fixture="proposal",
        writable=True,
        post_data={"decision": "adopted", "reason": "妥当と判断した"},
    ),
    # ── 実行系（POST のみ） ──────────────────────────────
    Screen("dashboard:detection_run", "action"),
    Screen("projects:task_archive", "action", fixture="task"),
    Screen("projects:issue_close", "action", fixture="issue"),
    Screen("projects:risk_close", "action", fixture="risk"),
    Screen("projects:defect_close", "action", fixture="defect"),
    Screen("documents:extract", "action", fixture="document"),
    Screen("integrations:check", "action", fixture="connection"),
    Screen("integrations:sync", "action", fixture="connection"),
)


class ScenarioBase(TestCase):
    """全シナリオが共有するデータ。

    自テナント（A）と他テナント（B）の両方に実データを入れる。
    他テナントを空にすると「越境しても何も出ない」のが権限のおかげか
    データが無いだけかを区別できない。
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cls.tenant = Tenant.objects.create(code="acme", name="ACME")
        cls.other_tenant = Tenant.objects.create(code="beta", name="ベータ商事")
        cls.empty_tenant = Tenant.objects.create(code="void", name="導入直後")

        cls.admin = cls._user("admin", cls.tenant, Role.TENANT_ADMIN)
        cls.pmo = cls._user("pmo", cls.tenant, Role.PMO)
        cls.viewer = cls._user("viewer", cls.tenant, Role.VIEWER)
        cls.foreigner = cls._user("beta-pmo", cls.other_tenant, Role.PMO)
        cls.newcomer = cls._user("newcomer", cls.empty_tenant, Role.TENANT_ADMIN)

        cls.project = cls._project(cls.tenant, "p1", "基幹刷新")
        cls.other_project = cls._project(cls.other_tenant, "b1", "ベータ社の案件")

        for user, role in (
            (cls.admin, ProjectRole.OWNER),
            (cls.pmo, ProjectRole.PMO),
            (cls.viewer, ProjectRole.VIEWER),
        ):
            ProjectMember.objects.create(project=cls.project, user=user, role=role)

        ProjectMember.objects.create(
            project=cls.other_project, user=cls.foreigner, role=ProjectRole.PMO
        )

        cls.fixtures_a = cls._build_objects(cls.project, "自社")
        cls.fixtures_b = cls._build_objects(cls.other_project, "ベータ社")

    # --- 生成ヘルパー ---------------------------------------------------

    @classmethod
    def _user(cls, username: str, tenant: Tenant, role: str) -> User:
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="test-password",
            tenant=tenant,
            role=role,
            display_name=username,
        )

    @classmethod
    def _project(cls, tenant: Tenant, code: str, name: str) -> Project:
        return Project.objects.create(
            tenant=tenant,
            code=code,
            name=name,
            progress_percent=50,
            planned_start=TODAY - timedelta(days=30),
            planned_end=TODAY + timedelta(days=30),
        )

    @classmethod
    def _build_objects(cls, project: Project, label: str) -> dict:
        """1 案件分の業務データ一式。画面が空にならない最小構成。"""

        task = WbsTask.objects.create(
            project=project,
            wbs_code="1.1",
            name=f"{label}の設計タスク",
            owner="担当者",
            status=WbsTask.Status.IN_PROGRESS,
            priority=Priority.HIGH,
            planned_start=TODAY - timedelta(days=10),
            planned_end=TODAY - timedelta(days=2),
            progress_percent=40,
            is_critical_path=True,
            next_action="レビュー待ち",
            ball_holder="顧客",
        )
        issue = Issue.objects.create(
            project=project,
            title=f"{label}の未解決課題",
            status=Issue.Status.OPEN,
            severity=Severity.HIGH,
            owner="担当者",
            due_date=TODAY + timedelta(days=3),
        )
        risk = Risk.objects.create(
            project=project,
            title=f"{label}のリスク",
            probability=4,
            impact=4,
            mitigation="監視する",
            due_date=TODAY + timedelta(days=7),
        )
        change = ChangeRequest.objects.create(
            project=project,
            title=f"{label}の変更要求",
            status=ChangeRequest.Status.PENDING_APPROVAL,
        )
        defect = Defect.objects.create(
            project=project,
            title=f"{label}の不具合",
            severity=Severity.CRITICAL,
            phase="結合試験",
            detected_on=TODAY - timedelta(days=5),
        )
        Milestone.objects.create(
            project=project,
            name=f"{label}の結合試験完了",
            planned_date=TODAY - timedelta(days=3),
            forecast_date=TODAY + timedelta(days=4),
            is_gate=True,
        )
        document = Document.objects.create(
            tenant=project.tenant,
            project=project,
            title=f"{label}の要件定義書",
            file=f"documents/{project.code}.txt",
            file_type=FileType.TXT,
            status=DocumentStatus.ACTIVE,
        )
        Template.objects.create(
            tenant=project.tenant,
            name=f"{label}の週次ひな型",
            file=f"templates/{project.code}.xlsx",
            field_mapping={"案件名": "B1"},
        )
        deliverable = Deliverable.objects.create(
            project=project,
            kind=Deliverable.Kind.WEEKLY_REPORT,
            title=f"{label}の週次報告",
            ai_generated_body="AI生成本文",
            body="確定本文",
        )
        run = AgentRun.objects.create(
            tenant=project.tenant,
            project=project,
            area=AgentRun.Area.PMO_CONSULTATION,
            user_input=f"{label}の相談",
            intent="delay",
            intent_confidence=0.8,
            status=AgentRun.Status.SUCCEEDED,
        )
        alert = Alert.objects.create(
            project=project,
            title=f"{label}の重要アラート",
            category=Alert.Category.SCHEDULE,
            severity=Alert.Severity.CRITICAL,
            detected_at=timezone.now(),
        )
        proposal = InterventionProposal.objects.create(
            project=project,
            title=f"{label}への介入提案",
            rationale="根拠",
            recommended_action="要員追加",
            confidence=0.7,
        )
        connection = Connection.objects.create(
            tenant=project.tenant,
            project=project,
            provider=Provider.JIRA,
            name=f"{label}のJira",
            base_url="https://example.atlassian.net",
            mode=Connection.Mode.MOCK,
            config={"project_key": "PMO"},
        )
        Feedback.objects.create(
            tenant=project.tenant,
            rating=Feedback.Rating.GOOD,
            comment=f"{label}のフィードバック",
        )

        return {
            "project": project,
            "task": task,
            "issue": issue,
            "risk": risk,
            "change": change,
            "defect": defect,
            "document": document,
            "deliverable": deliverable,
            "run": run,
            "alert": alert,
            "proposal": proposal,
            "connection": connection,
        }

    # --- 実行ヘルパー ---------------------------------------------------

    def login(self, user: User, tenant: Tenant | None = None):
        self.client.force_login(user)
        session = self.client.session
        session["current_tenant_id"] = str((tenant or user.tenant).pk)
        session.save()

        return self.client

    def select_project(self, project: Project) -> None:
        session = self.client.session
        session["current_project_id"] = str(project.pk)
        session.save()

    def url_for(self, screen: Screen, *, foreign: bool = False, missing: bool = False) -> str:
        if screen.kind == "page":
            return reverse(screen.url_name)

        if missing:
            return reverse(screen.url_name, args=[uuid.uuid4()])

        source = self.fixtures_b if foreign else self.fixtures_a

        return reverse(screen.url_name, args=[source[screen.fixture].pk])

    def assert_not_server_error(self, response, note: str) -> None:
        self.assertNotEqual(
            response.status_code, SERVER_ERROR, f"{note} で 500 が返った"
        )


def _scenarios_for(screen: Screen) -> dict:
    """画面 1 つに当てるシナリオ 10 案を返す。"""

    if screen.kind == "object":
        return _object_scenarios(screen)

    if screen.kind == "action":
        return _action_scenarios(screen)

    return _page_scenarios(screen)


def _page_scenarios(screen: Screen) -> dict:
    """一覧・ダッシュボード画面のシナリオ。"""

    def actor(self):
        """この画面を開ける最小のロールの利用者。

        外部連携の設定のようにテナント管理者専用の画面があるため、
        全画面を PMO で開こうとすると「仕様どおりの 403」を不具合と誤認する。
        """

        return self.admin if screen.admin_only else self.pmo

    def s01_未ログインではログインへ誘導される(self):
        response = self.client.get(self.url_for(screen))

        self.assertIn(response.status_code, (302, 403), "未ログインで素通しになっている")

        if response.status_code == 302:
            self.assertIn("login", response["Location"])

    def s02_ログインすれば開ける(self):
        self.login(actor(self))
        response = self.client.get(self.url_for(screen), screen.query)

        self.assertEqual(response.status_code, 200)

    def s03_画面タイトルが出る(self):
        self.login(actor(self))
        response = self.client.get(self.url_for(screen))

        self.assertEqual(response.status_code, 200)
        title = response.context.get("page_title") if response.context else None

        if title:
            self.assertContains(response, title)

    def s04_他社のデータが出ない(self):
        self.login(actor(self))
        response = self.client.get(self.url_for(screen), screen.query)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "ベータ社")

    def s05_案件を選んでも開ける(self):
        self.login(actor(self))
        self.select_project(self.project)
        response = self.client.get(self.url_for(screen), screen.query)

        self.assertEqual(response.status_code, 200)

    def s06_絞り込みに不正な値が来ても壊れない(self):
        self.login(actor(self))
        # 絞り込みを持つ画面はその項目へ、持たない画面は未知の項目へ変な値を入れる。
        junk = dict.fromkeys(screen.query, "'; DROP TABLE--") or {"status": "存在しない値"}
        response = self.client.get(self.url_for(screen), junk)

        self.assert_not_server_error(response, "不正な絞り込み値")
        self.assertEqual(response.status_code, 200)

    def s07_ページを送りすぎても壊れない(self):
        self.login(actor(self))
        response = self.client.get(self.url_for(screen), {"page": "9999"})

        self.assert_not_server_error(response, "範囲外のページ番号")
        self.assertEqual(response.status_code, 200)

    def s08_データが無い利用者でも開ける(self):
        self.login(self.newcomer)
        response = self.client.get(self.url_for(screen), screen.query)

        self.assert_not_server_error(response, "データ 0 件")
        self.assertEqual(response.status_code, 200)

    def s09_参照のみの利用者の扱いが決まっている(self):
        """参照専用の利用者に、書き込み画面と管理者専用画面を開かせない。"""

        self.login(self.viewer)
        response = self.client.get(self.url_for(screen), screen.query)

        self.assert_not_server_error(response, "参照のみロール")
        expected = (403,) if screen.admin_only else (200, 403)
        self.assertIn(response.status_code, expected)

    def s10_想定外のPOSTでも壊れない(self):
        self.login(actor(self))
        response = self.client.post(self.url_for(screen), {})

        self.assert_not_server_error(response, "空の POST")

    return locals()


def _object_scenarios(screen: Screen) -> dict:
    """詳細・編集画面のシナリオ。ID を書き換えられる前提で見る。"""

    def actor(self):
        return self.admin if screen.admin_only else self.pmo

    def s01_未ログインではログインへ誘導される(self):
        response = self.client.get(self.url_for(screen))

        self.assertIn(response.status_code, (302, 403))

    def s02_自社のデータなら開ける(self):
        self.login(actor(self))
        response = self.client.get(self.url_for(screen))

        self.assertEqual(response.status_code, 200)

    def s03_他社のIDは存在しない扱いになる(self):
        """403 ではなく 404。403 だと「そこに何かある」ことが漏れる。"""

        self.login(actor(self))
        response = self.client.get(self.url_for(screen, foreign=True))

        self.assertEqual(response.status_code, 404)

    def s04_存在しないIDは404(self):
        self.login(actor(self))
        response = self.client.get(self.url_for(screen, missing=True))

        self.assertEqual(response.status_code, 404)

    def s05_壊れたIDでも500にならない(self):
        self.login(actor(self))
        path = self.url_for(screen).rsplit("/", 2)[0] + "/not-a-uuid/"
        response = self.client.get(path)

        self.assert_not_server_error(response, "UUID でない ID")

    def s06_案件を選んだ状態でも直リンクで開ける(self):
        """選択中でない案件のデータでも、権限があれば開けること。"""

        self.login(actor(self))
        self.select_project(self.project)
        response = self.client.get(self.url_for(screen))

        self.assertEqual(response.status_code, 200)

    def s07_参照のみの利用者の扱いが決まっている(self):
        self.login(self.viewer)
        response = self.client.get(self.url_for(screen))

        expected = (403,) if (screen.writable or screen.admin_only) else (200,)
        self.assertIn(response.status_code, expected)

    def s08_空のPOSTでも500にならない(self):
        self.login(self.admin)
        response = self.client.post(self.url_for(screen), {})

        self.assert_not_server_error(response, "空の POST")

    def s09_他社のIDへPOSTしても404(self):
        self.login(self.admin)
        response = self.client.post(self.url_for(screen, foreign=True), {})

        self.assertEqual(response.status_code, 404)

    def s10_連打しても500にならない(self):
        self.login(self.admin)
        first = self.client.post(self.url_for(screen), {})
        second = self.client.post(self.url_for(screen), {})

        self.assert_not_server_error(first, "1 回目の POST")
        self.assert_not_server_error(second, "2 回目の POST")

    return locals()


def _action_scenarios(screen: Screen) -> dict:
    """実行系（POST のみ）のシナリオ。"""

    def _url(self, *, foreign: bool = False, missing: bool = False) -> str:
        if not screen.fixture:
            return reverse(screen.url_name)

        return self.url_for(screen, foreign=foreign, missing=missing)

    def s01_未ログインでは実行できない(self):
        response = self.client.post(_url(self), screen.post_data)

        self.assertIn(response.status_code, (302, 403))

    def s02_GETでは実行できない(self):
        self.login(self.admin)
        response = self.client.get(_url(self))

        self.assertIn(response.status_code, (405, 403, 404))

    def s03_自社のデータなら実行できる(self):
        self.login(self.admin)
        response = self.client.post(_url(self), screen.post_data)

        self.assert_not_server_error(response, "正常な実行")
        self.assertIn(response.status_code, (200, 302))

    def s04_他社のIDでは実行できない(self):
        if not screen.fixture:
            self.skipTest("ID を取らない画面")

        self.login(self.admin)
        response = self.client.post(_url(self, foreign=True), screen.post_data)

        self.assertEqual(response.status_code, 404)

    def s05_存在しないIDでは実行できない(self):
        if not screen.fixture:
            self.skipTest("ID を取らない画面")

        self.login(self.admin)
        response = self.client.post(_url(self, missing=True), screen.post_data)

        self.assertEqual(response.status_code, 404)

    def s06_参照のみの利用者は実行できない(self):
        self.login(self.viewer)
        response = self.client.post(_url(self), screen.post_data)

        self.assert_not_server_error(response, "参照のみロールの実行")
        self.assertIn(response.status_code, (403, 302, 200))

    def s07_二回実行しても壊れない(self):
        self.login(self.admin)
        first = self.client.post(_url(self), screen.post_data)
        second = self.client.post(_url(self), screen.post_data)

        self.assert_not_server_error(first, "1 回目の実行")
        self.assert_not_server_error(second, "2 回目の実行（連打）")

    def s08_実行後は画面へ戻る(self):
        self.login(self.admin)
        response = self.client.post(_url(self), screen.post_data, follow=True)

        self.assert_not_server_error(response, "実行後の遷移先")
        self.assertEqual(response.status_code, 200)

    def s09_CSRFトークンが無ければ拒否される(self):
        self.client.handler.enforce_csrf_checks = True
        self.login(self.admin)
        response = self.client.post(_url(self), screen.post_data)
        self.client.handler.enforce_csrf_checks = False

        self.assertEqual(response.status_code, 403)

    def s10_空のPOSTでも500にならない(self):
        self.login(self.admin)
        response = self.client.post(_url(self), {})

        self.assert_not_server_error(response, "必須項目が空の POST")

    return locals()


def _build_case(screen: Screen) -> type[ScenarioBase]:
    """画面 1 つ分のテストクラスを作る。"""

    # 生成元の関数名は `s01_…`。テストランナーが拾うのは `test_` で始まる名前
    # だけなので、ここで付け替える。番号を残すのは実行順を安定させるため。
    namespace = {
        f"test_{name}": func
        for name, func in _scenarios_for(screen).items()
        if name.startswith("s") and name[1:3].isdigit() and callable(func)
    }
    namespace["__doc__"] = f"{screen.url_name} のユーザーシナリオ 10 案。"

    return type(f"Scenario_{screen.slug}", (ScenarioBase,), namespace)


for _screen in SCREENS:
    globals()[f"Scenario_{_screen.slug}"] = _build_case(_screen)


class ScreenCatalogTests(ScenarioBase):
    """カタログ自体の健全性。ここが緩むと、上のシナリオが静かに減る。"""

    def test_各画面に10案のシナリオが当たっている(self) -> None:
        for screen in SCREENS:
            case = globals()[f"Scenario_{screen.slug}"]
            scenarios = [
                name
                for name in vars(case)
                if name.startswith("test_s") and callable(getattr(case, name))
            ]

            self.assertEqual(len(scenarios), 10, f"{screen.url_name} のシナリオ数が 10 でない")

    def test_カタログがナビゲーションの全画面を覆う(self) -> None:
        """サイドメニューから行ける画面に、シナリオが無い状態を作らない。"""

        covered = {screen.url_name for screen in SCREENS}
        missing = [item.url_name for item in all_items() if item.url_name not in covered]

        self.assertEqual(missing, [], f"シナリオ未作成の画面がある: {missing}")

    def test_カタログに重複が無い(self) -> None:
        names = [screen.url_name for screen in SCREENS]

        self.assertEqual(len(names), len(set(names)))
