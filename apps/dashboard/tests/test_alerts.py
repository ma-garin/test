"""アラートの一覧と、状態を更新する判断。

アラートは検知して保存するだけで、状態を戻す経路が Django admin にしか
無かった。放置されたアラートは 3 つの壊れ方を同時に起こす。

- `Alert.lead_time_days` が常に None で、PoC 受入条件「予兆検知の先行日数」を実測できない
- 未対応が残り続け、ヘルススコアを恒久的に下げる（`PENALTY_OPEN_ALERT`）
- 重複排除（`ACTIVE_ALERT_STATUSES`）により、同じ対象が二度と検知されない

そのため、ここで固定するのは「押せること」だけではない。押せてはいけない人が
押せないこと（権限・テナント越境）と、押した結果が記録として残ること
（確認日時・操作ログ・二重判断の禁止）を対で確かめる。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.audit.models import OperationLog
from apps.core.pagination import PAGE_SIZE
from apps.dashboard.models import Alert
from apps.dashboard.services.alerts import DECIDE_ACTION
from apps.projects.models import Project, ProjectMember

#: 2 ページ目が必ず出る件数。
TOTAL = 60


class AlertTestBase(TestCase):
    def setUp(self) -> None:
        self.now = timezone.now()
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other_tenant = Tenant.objects.create(code="globex", name="Globex")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")

        self.editor = self._member("editor", Role.PMO, ProjectRole.PMO)
        self.viewer = self._member("viewer", Role.VIEWER, ProjectRole.VIEWER)

        self.alert = self._alert(self.project, title="クリティカルパスに遅延")

    def _member(self, name: str, role: str, project_role: str) -> User:
        user = User.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="test-password",
            tenant=self.tenant,
            role=role,
        )
        ProjectMember.objects.create(project=self.project, user=user, role=project_role)

        return user

    def _alert(self, project: Project, **overrides) -> Alert:
        values = {
            "project": project,
            "category": Alert.Category.SCHEDULE,
            "severity": Alert.Severity.CRITICAL,
            "status": Alert.Status.OPEN,
            "title": "アラート",
            "detail": "先行タスクの遅れが後続へ波及します。",
            # 検知は 4 日前。確認したときに先行日数が 0 にならない値にしておく。
            "detected_at": self.now - timedelta(days=4),
            "evidence": {
                "rule": "critical_path",
                "reason": "遅延 12日 が しきい値 5日 を超えました。",
                "observed": {"delay_days": 12, "impacted_tasks": 3},
                "threshold": {"delay_days": 5, "min_impacted_tasks": 2},
            },
        }
        values.update(overrides)

        return Alert.objects.create(**values)

    def _decide(self, alert: Alert, status: str, **payload):
        return self.client.post(
            reverse("dashboard:alert_decide", args=[alert.pk]), {"status": status, **payload}
        )


class AlertListTests(AlertTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.client.force_login(self.editor)
        self.url = reverse("dashboard:alert_list")

    def test_一覧に検知根拠が読める形で出る(self):
        """根拠の読めないアラートは「AIが何か言っている」だけで判断に使えない。"""

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "遅延 12日 が しきい値 5日 を超えました。")
        self.assertContains(response, "遅延日数")
        self.assertContains(response, "影響タスク数の下限")

    def test_状態と重要度と分類で絞り込める(self):
        quality = self._alert(
            self.project,
            title="バグ率が急増",
            category=Alert.Category.QUALITY,
            severity=Alert.Severity.WARNING,
            status=Alert.Status.RESOLVED,
        )

        def titles(params):
            response = self.client.get(self.url, params)

            self.assertEqual(response.status_code, 200)

            return [row.alert.title for row in response.context["board"].rows]

        self.assertEqual(titles({"status": Alert.Status.RESOLVED}), [quality.title])
        self.assertEqual(titles({"severity": Alert.Severity.CRITICAL}), [self.alert.title])
        self.assertEqual(titles({"category": Alert.Category.QUALITY}), [quality.title])

    def test_不正な値では絞り込まない(self):
        for params in ({"status": "unknown"}, {"severity": "とても重大"}, {"category": "9"}):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["board"].total, Alert.objects.count())

    def test_総件数は絞り込み後の全件でページ表示と食い違わない(self):
        Alert.objects.all().delete()

        for index in range(TOTAL):
            self._alert(
                self.project,
                title=f"アラート{index}",
                severity=Alert.Severity.CRITICAL if index < 55 else Alert.Severity.INFO,
            )

        first = self.client.get(self.url, {"severity": Alert.Severity.CRITICAL})
        second = self.client.get(self.url, {"severity": Alert.Severity.CRITICAL, "page": 2})

        self.assertEqual(first.context["board"].total, 55)
        self.assertEqual(second.context["board"].total, 55)
        self.assertEqual(len(first.context["board"].rows), PAGE_SIZE)
        self.assertEqual(len(second.context["board"].rows), 55 - PAGE_SIZE)
        self.assertIn("severity=critical", first.context["page_query"])

    def test_アラートが無ければ空状態を出す(self):
        Alert.objects.all().delete()

        response = self.client.get(self.url)

        self.assertContains(response, "アラートはまだありません")

    def test_他テナントのアラートは一覧に出ない(self):
        foreign = Project.objects.create(tenant=self.other_tenant, code="gx", name="他社案件")
        self._alert(foreign, title="他テナントのアラート")

        response = self.client.get(self.url)

        self.assertNotContains(response, "他テナントのアラート")
        self.assertEqual(response.context["board"].total, 1)

    def test_参照だけの利用者には判断ボタンを出さない(self):
        """画面で隠すだけでは防御にならないが、押せないボタンを見せる必要も無い。"""

        self.client.force_login(self.viewer)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["board"].rows[0].can_decide)
        self.assertNotContains(response, "対応済みにする")


class AlertDecisionTests(AlertTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.client.force_login(self.editor)

    def test_確認すると確認日時が入り先行日数が実測できる(self):
        response = self._decide(self.alert, Alert.Status.ACKNOWLEDGED, note="内容を確認した")

        self.assertEqual(response.status_code, 302)

        self.alert.refresh_from_db()

        self.assertEqual(self.alert.status, Alert.Status.ACKNOWLEDGED)
        self.assertIsNotNone(self.alert.acknowledged_at)
        # 4 日前に検知したものを今確認したので、先行日数は 4 日。
        self.assertEqual(self.alert.lead_time_days, 4)

    def test_解消へ直接進めても確認日時は必ず残る(self):
        """二段階を強制しない。ただし記録しないと先行日数を永久に測れない。"""

        self._decide(self.alert, Alert.Status.RESOLVED, note="恒久対応を実施")

        self.alert.refresh_from_db()

        self.assertEqual(self.alert.status, Alert.Status.RESOLVED)
        self.assertEqual(self.alert.lead_time_days, 4)

    def test_確認済みから解消へ進めても最初の確認日時を上書きしない(self):
        self._decide(self.alert, Alert.Status.ACKNOWLEDGED)
        self.alert.refresh_from_db()
        acknowledged_at = self.alert.acknowledged_at

        self._decide(self.alert, Alert.Status.RESOLVED, note="対応完了")
        self.alert.refresh_from_db()

        self.assertEqual(self.alert.status, Alert.Status.RESOLVED)
        self.assertEqual(self.alert.acknowledged_at, acknowledged_at)

    def test_判断は操作ログに残る(self):
        self._decide(self.alert, Alert.Status.ACKNOWLEDGED, note="担当へ連携済み")

        log = OperationLog.objects.get(action=DECIDE_ACTION)

        self.assertEqual(log.user, self.editor)
        self.assertEqual(log.project, self.project)
        self.assertIn(self.alert.title, log.target)
        self.assertIn("担当へ連携済み", log.detail)
        # 先行日数はアラート側に履歴が残らないため、ログ本文に残す。
        self.assertIn("4日", log.detail)

    def test_確定済みのアラートは二重に更新できない(self):
        self._decide(self.alert, Alert.Status.RESOLVED, note="対応完了")
        self.alert.refresh_from_db()
        decided_at = self.alert.acknowledged_at

        response = self._decide(self.alert, Alert.Status.DISMISSED, note="やっぱり対象外")

        self.assertEqual(response.status_code, 302)

        self.alert.refresh_from_db()

        self.assertEqual(self.alert.status, Alert.Status.RESOLVED)
        self.assertEqual(self.alert.acknowledged_at, decided_at)
        self.assertEqual(OperationLog.objects.filter(action=DECIDE_ACTION).count(), 1)

    def test_確認済みをもう一度確認しても記録は増えない(self):
        self._decide(self.alert, Alert.Status.ACKNOWLEDGED)
        self._decide(self.alert, Alert.Status.ACKNOWLEDGED)

        self.assertEqual(OperationLog.objects.filter(action=DECIDE_ACTION).count(), 1)

    def test_対象外にするには理由が要る(self):
        """AI の検知を人が否定する判断。理由が無いと誤検知か見落としか分からない。"""

        response = self._decide(self.alert, Alert.Status.DISMISSED, note="  ")

        self.assertEqual(response.status_code, 302)

        self.alert.refresh_from_db()

        self.assertEqual(self.alert.status, Alert.Status.OPEN)
        self.assertFalse(OperationLog.objects.filter(action=DECIDE_ACTION).exists())

    def test_未対応へは戻せない(self):
        self._decide(self.alert, Alert.Status.OPEN, note="戻す")

        self.alert.refresh_from_db()

        self.assertEqual(self.alert.status, Alert.Status.OPEN)
        self.assertIsNone(self.alert.acknowledged_at)

    def test_判断後も絞り込み条件を保ったまま一覧へ戻る(self):
        response = self.client.post(
            reverse("dashboard:alert_decide", args=[self.alert.pk]),
            {"status": Alert.Status.ACKNOWLEDGED, "back": "severity=critical&page=2"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("severity=critical", response["Location"])
        self.assertIn("page=2", response["Location"])

    def test_戻り先に外部URLは指定できない(self):
        """`back` は利用者が触れる値。そのまま飛ばすと外部サイトへ誘導できてしまう。"""

        response = self.client.post(
            reverse("dashboard:alert_decide", args=[self.alert.pk]),
            {"status": Alert.Status.ACKNOWLEDGED, "back": "https://evil.example.com/"},
        )

        self.assertEqual(response["Location"], reverse("dashboard:alert_list"))

    def test_GETでは状態を変えられない(self):
        response = self.client.get(reverse("dashboard:alert_decide", args=[self.alert.pk]))

        self.assertEqual(response.status_code, 405)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, Alert.Status.OPEN)


class AlertPermissionTests(AlertTestBase):
    def test_参照専用ロールは状態を更新できず状態も変わらない(self):
        self.client.force_login(self.viewer)

        response = self._decide(self.alert, Alert.Status.ACKNOWLEDGED, note="確認した")

        self.assertEqual(response.status_code, 403)

        self.alert.refresh_from_db()

        self.assertEqual(self.alert.status, Alert.Status.OPEN)
        self.assertIsNone(self.alert.acknowledged_at)
        self.assertFalse(OperationLog.objects.filter(action=DECIDE_ACTION).exists())

    def test_他テナントのアラートは404で存在も漏らさない(self):
        foreign_project = Project.objects.create(
            tenant=self.other_tenant, code="gx", name="他社案件"
        )
        foreign = self._alert(foreign_project, title="他テナントのアラート")

        self.client.force_login(self.editor)
        response = self._decide(foreign, Alert.Status.ACKNOWLEDGED, note="確認した")

        self.assertEqual(response.status_code, 404)

        foreign.refresh_from_db()

        self.assertEqual(foreign.status, Alert.Status.OPEN)

    def test_非メンバーの案件のアラートは404になる(self):
        """同じテナントでも、参加していない案件は「見えない」ではなく「無い」。"""

        other_project = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")
        alert = self._alert(other_project, title="参加していない案件のアラート")
        outsider = User.objects.create_user(
            username="outsider",
            email="outsider@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.PMO,
        )
        ProjectMember.objects.create(project=self.project, user=outsider, role=ProjectRole.PMO)

        self.client.force_login(outsider)
        response = self._decide(alert, Alert.Status.ACKNOWLEDGED, note="確認した")

        self.assertEqual(response.status_code, 404)

        alert.refresh_from_db()

        self.assertEqual(alert.status, Alert.Status.OPEN)

    def test_確認するとヘルススコアの減点と重複排除が解ける(self):
        """未対応のまま残ると、減点が続き、同じ対象が二度と再検知されない。"""

        from apps.dashboard.services.detection.runner import ACTIVE_ALERT_STATUSES
        from apps.dashboard.services.overview import build_overview

        self.client.force_login(self.editor)
        self._decide(self.alert, Alert.Status.RESOLVED, note="対応完了")

        overview = build_overview(Project.objects.filter(pk=self.project.pk))

        self.assertEqual(overview.summaries[0].open_alerts, 0)
        self.alert.refresh_from_db()
        self.assertNotIn(self.alert.status, ACTIVE_ALERT_STATUSES)
