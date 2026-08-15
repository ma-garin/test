"""秘密値のマスク。

監査ログと同期履歴は長期保存する前提なので、一度平文で入ると回収できない。
このシステムが実際に扱う認証情報の形（OpenAI・Slack・Teams・Jira・GitHub）を
すべて伏せられることを固定する。
"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.audit.models import OperationLog, mask_secrets
from apps.integrations.models import Connection, Provider, SyncJob

def _sample(prefix: str, body: str) -> str:
    """検知パターンに一致するダミー値を、実行時に組み立てる。

    ソースへ完全な形で書くと、GitHub の push protection が
    「本物の認証情報が混入した」とみなして push を拒否する。
    テストが必要とするのは *実行時の文字列* なので、置き場所だけを分ける。
    """

    return prefix + body


#: 実際に平文で流れうる形。値そのものはダミー。
SECRETS = {
    "OpenAI APIキー": _sample("sk-", "proj-abcdefghijklmnopqrstuvwxyz0123456789"),
    "OpenAI 組織ID": _sample("org-", "abcdefghijklmnop"),
    "OpenAI プロジェクトID": _sample("proj", "_abcdefghijklmnop"),
    "Slack Webhook": _sample("https://hooks.slack.com/", "services/T00000000/B00000000/XXXXXXXXXXXXXXXX"),
    "Slack トークン": _sample("xox", "b-123456789012-1234567890123-abcdefghijklmnopqrstuvwx"),
    "Teams Webhook": _sample(
        "https://acme.webhook.office.com/", "webhookb2/abc-def@ghi/IncomingWebhook/xyz"
    ),
    "Jira トークン": _sample("ATATT", "3xFfGF0abcdefghijklmnopqrstuvwxyz"),
    "GitHub PAT": _sample("ghp", "_abcdefghijklmnopqrstuvwxyz0123456789"),
    "GitHub 細粒度PAT": _sample("github", "_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz"),
    "key=value 形式": _sample("api_key=", "super-secret-value"),
}


class MaskSecretsTests(TestCase):
    def test_扱う認証情報の形をすべて伏せる(self):
        for label, secret in SECRETS.items():
            with self.subTest(kind=label):
                masked = mask_secrets(f"同期に失敗しました: {secret} を確認してください")

                self.assertNotIn(secret, masked)
                self.assertIn("[REDACTED]", masked)

    def test_ふつうの文は変えない(self):
        text = "課題 ISS-12 の担当者が未設定です。期限は 2026-08-31 です。"

        self.assertEqual(mask_secrets(text), text)


class OperationLogMaskingTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")

    def test_保存時にマスクされる(self):
        log = OperationLog.objects.create(
            tenant=self.tenant,
            action="外部連携の同期",
            target=SECRETS["Slack Webhook"],
            detail=f"送信先 {SECRETS['Jira トークン']} が拒否されました",
        )
        log.refresh_from_db()

        self.assertNotIn(SECRETS["Slack Webhook"], log.target)
        self.assertNotIn(SECRETS["Jira トークン"], log.detail)


class SyncJobMaskingTests(TestCase):
    """同期履歴は画面へそのまま出る。ここが漏れると秘密値が表示される。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.connection = Connection.objects.create(
            tenant=self.tenant,
            provider=Provider.JIRA,
            name="Jira",
            base_url="https://acme.atlassian.net",
        )

    def test_入れ子の詳細もマスクする(self):
        job = SyncJob.objects.create(
            connection=self.connection,
            message=f"失敗: {SECRETS['GitHub PAT']}",
            detail={
                "failures": [
                    {"key": "ISS-1", "reason": f"認証に失敗: {SECRETS['Jira トークン']}"},
                ],
                "endpoint": SECRETS["Slack Webhook"],
            },
        )
        job.refresh_from_db()

        serialized = str(job.detail) + job.message

        for secret in (
            SECRETS["GitHub PAT"],
            SECRETS["Jira トークン"],
            SECRETS["Slack Webhook"],
        ):
            with self.subTest(secret=secret[:12]):
                self.assertNotIn(secret, serialized)

    def test_件数などの値は壊さない(self):
        job = SyncJob.objects.create(
            connection=self.connection,
            detail={"fetched": 12, "unmapped": ["状態: 保留"], "ok": True},
        )
        job.refresh_from_db()

        self.assertEqual(job.detail["fetched"], 12)
        self.assertEqual(job.detail["unmapped"], ["状態: 保留"])
        self.assertIs(job.detail["ok"], True)
