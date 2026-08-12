"""通知ルールとコネクタのテスト。

検証したいのは「送れること」ではなく、**送りすぎないこと**と
**秘密が漏れないこと**。この 2 つが崩れると、実務では通知が読まれなくなるか、
Webhook URL が履歴経由で流出する。

外部通信は一切行わない。LIVE モードの経路は `sys.modules` へ偽の requests を
差し込んで検証する（本物が入っていても絶対にネットワークへ出ないようにするため）。
"""

from __future__ import annotations

import sys
import types
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.dashboard.models import Alert, InterventionProposal
from apps.integrations.models import Connection, NotificationLog, Provider
from apps.integrations.services.connectors.base import ConnectorError
from apps.integrations.services.connectors.slack import SlackConnector
from apps.integrations.services.connectors.teams import TeamsConnector
from apps.integrations.services.notify import (
    collect_notifications,
    notify_connector,
    send_pending_notifications,
)
from apps.projects.models import ChangeRequest, Project, WbsTask

SECRET_URL = "https://hooks.slack.com/services/T00000/B00000/zzTOPSECRETzz"


def fake_requests(post) -> types.ModuleType:
    """`import requests` が拾う偽モジュール。"""

    module = types.ModuleType("requests")
    module.post = post

    return module


class NotifyTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(
            tenant=self.tenant, code="p1", name="基幹刷新プロジェクト"
        )
        self.connection = Connection.objects.create(
            tenant=self.tenant,
            provider=Provider.SLACK,
            name="PMO通知",
            mode=Connection.Mode.MOCK,
            config={"channel": "#pmo-alerts"},
        )
        self.now = timezone.now()

    # ── 生成ヘルパ ──────────────────────────────────────────

    def _alert(self, **kwargs) -> Alert:
        defaults = {
            "project": self.project,
            "category": Alert.Category.SCHEDULE,
            "severity": Alert.Severity.CRITICAL,
            "title": "クリティカルパスに3週間の遅延",
            "detail": "結合テスト工程の着手が遅れています",
            "detected_at": self.now,
        }

        return Alert.objects.create(**{**defaults, **kwargs})

    def _proposal(self, **kwargs) -> InterventionProposal:
        defaults = {
            "project": self.project,
            "title": "結合テストの要員を2名追加する",
            "rationale": "直近3スプリントの消化率が計画比62%",
            "confidence": 0.82,
        }

        return InterventionProposal.objects.create(**{**defaults, **kwargs})

    def _task(self, *, code: str, days_overdue: int, **kwargs) -> WbsTask:
        today = timezone.localtime(self.now).date()

        return WbsTask.objects.create(
            project=self.project,
            wbs_code=code,
            name=f"タスク{code}",
            owner="山田",
            planned_end=today - timedelta(days=days_overdue),
            **kwargs,
        )

    def _backdate(self, instance, *, days: int) -> None:
        """auto_now_add を迂回して起票日時を過去にする。"""

        instance.__class__.objects.filter(pk=instance.pk).update(
            created_at=self.now - timedelta(days=days)
        )

    def _sent(self) -> list[NotificationLog]:
        return list(NotificationLog.objects.filter(status=NotificationLog.Status.SENT))


class MockModeTests(NotifyTestBase):
    def test_モックモードでは実送信せず履歴だけが残る(self):
        self._alert()

        # 実 API 経路が呼ばれたら即失敗させる。
        def explode(*args, **kwargs):  # pragma: no cover - 呼ばれないことが検証対象
            raise AssertionError("モックモードで外部送信が行われた")

        with mock.patch.dict(sys.modules, {"requests": fake_requests(explode)}):
            summary = send_pending_notifications(self.tenant, now=self.now)

        self.assertEqual(summary.sent, 1)

        log = NotificationLog.objects.get()
        self.assertEqual(log.status, NotificationLog.Status.SENT)
        self.assertEqual(log.channel, "#pmo-alerts")
        self.assertIn("クリティカルパス", log.title)
        self.assertIn("対応期限", log.body)
        self.assertIn("検知時刻", log.body)
        self.assertIn("基幹刷新プロジェクト", log.body)
        self.assertIsNotNone(log.sent_at)

    def test_資格情報が無い実APIモードは失敗ではなく送信せずとして残る(self):
        self._alert()
        self.connection.mode = Connection.Mode.LIVE
        self.connection.credential_env = "MISSING_SLACK_WEBHOOK"
        self.connection.save(update_fields=["mode", "credential_env"])

        with mock.patch.dict("os.environ", {}, clear=False):
            summary = send_pending_notifications(self.tenant, now=self.now)

        log = NotificationLog.objects.get()
        self.assertEqual(summary.failed, 1)
        self.assertEqual(log.status, NotificationLog.Status.SKIPPED)
        self.assertIn("MISSING_SLACK_WEBHOOK", log.error)


class SuppressionTests(NotifyTestBase):
    def test_同じ対象は二度通知されない(self):
        self._alert()

        first = send_pending_notifications(self.tenant, now=self.now)
        second = send_pending_notifications(self.tenant, now=self.now)

        self.assertEqual(first.sent, 1)
        self.assertEqual(second.sent, 0)
        self.assertEqual(second.suppressed, 1)
        self.assertEqual(len(self._sent()), 1)

    def test_一覧通知は未通知の対象だけを本文に載せる(self):
        self._task(code="1.1", days_overdue=10)
        self._task(code="1.2", days_overdue=8)

        send_pending_notifications(self.tenant, now=self.now)

        # 2 件をまとめて 1 通。抑止キーは対象ごとに残る。
        self.assertEqual(len(self._sent()), 2)
        first_body = NotificationLog.objects.exclude(body="").get().body
        self.assertIn("タスク1.1", first_body)
        self.assertIn("タスク1.2", first_body)

        self._task(code="1.3", days_overdue=6)
        summary = send_pending_notifications(self.tenant, now=self.now)

        self.assertEqual(summary.sent, 1)
        latest = NotificationLog.objects.exclude(body="").order_by("-created_at").first()
        self.assertIn("タスク1.3", latest.body)
        self.assertNotIn("タスク1.1", latest.body)
        self.assertIn("（1件）", latest.title)

    def test_接続ごとに抑止するので別の通知先へは届く(self):
        self._alert()
        teams = Connection.objects.create(
            tenant=self.tenant,
            provider=Provider.TEAMS,
            name="経営向け",
            mode=Connection.Mode.MOCK,
        )

        summary = send_pending_notifications(self.tenant, now=self.now)

        self.assertEqual(summary.sent, 2)
        self.assertEqual(NotificationLog.objects.filter(connection=teams).count(), 1)


class ThresholdTests(NotifyTestBase):
    def test_重要度が注意どまりのアラートは通知しない(self):
        self._alert(severity=Alert.Severity.WARNING)

        summary = send_pending_notifications(self.tenant, now=self.now)

        self.assertEqual(summary.sent, 0)
        self.assertEqual(NotificationLog.objects.count(), 0)

    def test_確認済みのアラートは通知しない(self):
        self._alert(status=Alert.Status.ACKNOWLEDGED)

        self.assertEqual(send_pending_notifications(self.tenant, now=self.now).sent, 0)

    def test_信頼度がしきい値未満の提案は通知しない(self):
        self._proposal(confidence=0.2)

        self.assertEqual(send_pending_notifications(self.tenant, now=self.now).sent, 0)

    def test_信頼度が未算出のルールベース提案は通知する(self):
        self._proposal(confidence=None)

        send_pending_notifications(self.tenant, now=self.now)

        log = NotificationLog.objects.get()
        self.assertIn("判断が必要", log.title)
        self.assertIn("未算出", log.body)
        self.assertIn("判断が必要です", log.body)

    def test_信頼度が十分な提案は根拠と信頼度を載せて通知する(self):
        self._proposal(confidence=0.82)

        send_pending_notifications(self.tenant, now=self.now)

        body = NotificationLog.objects.get().body
        self.assertIn("消化率が計画比62%", body)
        self.assertIn("82%", body)

    def test_超過日数がしきい値未満のタスクは通知しない(self):
        self._task(code="2.1", days_overdue=1)

        self.assertEqual(send_pending_notifications(self.tenant, now=self.now).sent, 0)

    def test_完了済みタスクは期限を過ぎていても通知しない(self):
        self._task(code="2.2", days_overdue=10, status=WbsTask.Status.DONE)

        self.assertEqual(send_pending_notifications(self.tenant, now=self.now).sent, 0)

    def test_滞留していない承認待ちは通知しない(self):
        change = ChangeRequest.objects.create(
            project=self.project,
            title="外部連携方式の変更",
            status=ChangeRequest.Status.PENDING_APPROVAL,
        )
        self.assertIsNotNone(change.pk)

        summary = send_pending_notifications(self.tenant, now=self.now)

        self.assertEqual(summary.sent, 0)


class StaleApprovalTests(NotifyTestBase):
    def test_滞留した提案と変更要求が一覧で通知される(self):
        proposal = self._proposal(confidence=0.9)
        change = ChangeRequest.objects.create(
            project=self.project,
            title="外部連携方式の変更",
            status=ChangeRequest.Status.PENDING_APPROVAL,
        )
        self._backdate(proposal, days=9)
        self._backdate(change, days=5)

        notifications = collect_notifications(self.tenant, now=self.now)
        stale = [note for note in notifications if note.kind == "stale"]

        self.assertEqual(len(stale), 1)
        body = stale[0].body(tuple(range(len(stale[0].keys))))
        self.assertIn("AI介入提案", body)
        self.assertIn("変更要求", body)
        self.assertIn("9日 未判断", body)

    def test_作成通知と滞留通知は互いを抑止しない(self):
        proposal = self._proposal(confidence=0.9)
        self._backdate(proposal, days=9)

        summary = send_pending_notifications(self.tenant, now=self.now)

        triggers = {log.trigger.split(":")[0] for log in self._sent()}
        self.assertEqual(summary.sent, 2)
        self.assertEqual(triggers, {"proposal", "stale"})


class PayloadTests(NotifyTestBase):
    def test_SlackはBlockKit形式へ整形する(self):
        payload = SlackConnector(self.connection).build_payload(
            title="[重大アラート] 遅延", body="内容: <b>3週間</b> & 要対応", channel="#pmo"
        )

        self.assertEqual(payload["blocks"][0]["type"], "header")
        self.assertEqual(payload["blocks"][1]["text"]["type"], "mrkdwn")
        self.assertIn("&lt;b&gt;", payload["blocks"][1]["text"]["text"])
        self.assertIn("&amp;", payload["blocks"][1]["text"]["text"])
        self.assertEqual(payload["channel"], "#pmo")

    def test_TeamsはMessageCard形式へ整形する(self):
        connection = Connection.objects.create(
            tenant=self.tenant, provider=Provider.TEAMS, name="Teams", mode=Connection.Mode.MOCK
        )
        payload = TeamsConnector(connection).build_payload(
            title="[重大アラート] 遅延", body="1行目\n2行目", channel=""
        )

        self.assertEqual(payload["@type"], "MessageCard")
        self.assertEqual(payload["summary"], "[重大アラート] 遅延")
        self.assertIn("<br>", payload["text"])

    def test_接続に対応するコネクタが選ばれる(self):
        self.assertIsInstance(notify_connector(self.connection), SlackConnector)

    def test_通知に対応しないプロバイダは弾く(self):
        jira = Connection.objects.create(
            tenant=self.tenant, provider=Provider.JIRA, name="Jira", mode=Connection.Mode.MOCK
        )

        with self.assertRaises(ConnectorError):
            notify_connector(jira)


class SecretLeakTests(NotifyTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.connection.mode = Connection.Mode.LIVE
        self.connection.credential_env = "TEST_SLACK_WEBHOOK"
        self.connection.save(update_fields=["mode", "credential_env"])

    def _send_with(self, post):
        with mock.patch.dict("os.environ", {"TEST_SLACK_WEBHOOK": SECRET_URL}):
            with mock.patch.dict(sys.modules, {"requests": fake_requests(post)}):
                return notify_connector(self.connection).send(
                    title="件名", body="本文", trigger="alert:1"
                )

    def test_例外文言にWebhookURLが残らない(self):
        def raising_post(url, **kwargs):
            # requests の例外は URL を含むことがある。その状況を再現する。
            raise RuntimeError(f"HTTPSConnectionPool: failed to reach url: {url}")

        result = self._send_with(raising_post)

        self.assertFalse(result.ok)
        self.assertNotIn(SECRET_URL, result.message)
        self.assertNotIn("zzTOPSECRETzz", result.message)

    def test_HTTPエラー時も履歴にWebhookURLが残らない(self):
        def failing_post(url, **kwargs):
            return types.SimpleNamespace(status_code=500, text=url)

        result = self._send_with(failing_post)
        log = NotificationLog.objects.get()

        self.assertFalse(result.ok)
        self.assertEqual(log.status, NotificationLog.Status.FAILED)
        self.assertIn("500", log.error)

        haystack = " ".join([log.error, log.title, log.body, log.channel, log.trigger])
        self.assertNotIn("zzTOPSECRETzz", haystack)

    def test_成功時も履歴にWebhookURLを残さない(self):
        def ok_post(url, **kwargs):
            return types.SimpleNamespace(status_code=200, text="ok")

        result = self._send_with(ok_post)
        log = NotificationLog.objects.get()

        self.assertTrue(result.ok)
        self.assertEqual(log.status, NotificationLog.Status.SENT)
        self.assertNotIn("zzTOPSECRETzz", " ".join([log.error, log.title, log.body]))

    def test_タイムアウトを必ず指定して送る(self):
        captured: dict = {}

        def capturing_post(url, **kwargs):
            captured.update(kwargs)

            return types.SimpleNamespace(status_code=200)

        self._send_with(capturing_post)

        self.assertIn("timeout", captured)
        self.assertGreater(captured["timeout"], 0)

    def test_疎通確認では実際に送信しない(self):
        def explode(*args, **kwargs):  # pragma: no cover - 呼ばれないことが検証対象
            raise AssertionError("check() が実送信した")

        with mock.patch.dict("os.environ", {"TEST_SLACK_WEBHOOK": SECRET_URL}):
            with mock.patch.dict(sys.modules, {"requests": fake_requests(explode)}):
                status = notify_connector(self.connection).check()

        self.assertTrue(status.ok)
        self.assertNotIn("zzTOPSECRETzz", status.message)
        self.assertEqual(NotificationLog.objects.count(), 0)

    def test_不正な形式のWebhookURLは疎通確認で弾く(self):
        with mock.patch.dict("os.environ", {"TEST_SLACK_WEBHOOK": "http://example.com/hook"}):
            status = notify_connector(self.connection).check()

        self.assertFalse(status.ok)
        self.assertIn("https", status.message)

    def test_資格情報が無ければ疎通確認で環境変数名だけを返す(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            status = notify_connector(self.connection).check()

        self.assertFalse(status.ok)
        self.assertIn("TEST_SLACK_WEBHOOK", status.message)


class CommandTests(NotifyTestBase):
    def _run(self, *args) -> str:
        out = StringIO()
        call_command("send_notifications", *args, stdout=out)

        return out.getvalue()

    def test_未通知のものだけを送る(self):
        self._alert()

        first = self._run("--tenant", "acme")
        second = self._run("--tenant", "acme")

        self.assertIn("送信 1", first)
        self.assertIn("送信 0", second)
        self.assertIn("抑止 1", second)
        self.assertEqual(len(self._sent()), 1)

    def test_dry_runでは履歴を残さない(self):
        self._alert()

        output = self._run("--tenant", "acme", "--dry-run")

        self.assertIn("[alert]", output)
        self.assertEqual(NotificationLog.objects.count(), 0)

    def test_通知先が無ければ警告して終わる(self):
        self.connection.delete()
        self._alert()

        self.assertIn("通知先", self._run("--tenant", "acme"))

    def test_存在しないテナントはエラーになる(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._run("--tenant", "unknown")
