"""秘密値マスクのテスト。

「認証情報を UI、ログ、引継ぎ資料に露出しない」ため、監査ログは保存時に
必ずマスクを通す。マスク漏れは後から回収できないので、保存経路で検証する。
"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.audit.models import Feedback, OperationLog, mask_secrets


class MaskSecretsTests(TestCase):
    def test_OpenAIのAPIキーを伏せる(self):
        # 秘密値だけを差し替え、前後の文脈は残す。どの設定を触ったかは追跡できる必要がある。
        self.assertEqual(mask_secrets("key=sk-abcdefghijklmnop"), "key=[REDACTED]")

    def test_組織IDとプロジェクトIDを伏せる(self):
        masked = mask_secrets("org-abcdefghij proj_abcdefghij")

        self.assertNotIn("abcdefghij", masked)

    def test_キー名付きの値を伏せる(self):
        self.assertEqual(mask_secrets("password: hunter2000"), "[REDACTED]")

    def test_通常の文章は変えない(self):
        text = "結合試験の進捗を確認しました。"

        self.assertEqual(mask_secrets(text), text)

    def test_空文字でも落ちない(self):
        self.assertEqual(mask_secrets(""), "")


class OperationLogMaskingTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")

    def test_保存時に詳細がマスクされる(self):
        log = OperationLog.objects.create(
            tenant=self.tenant,
            action="設定更新",
            detail="OPENAI_API_KEY=sk-thisisasecretvalue へ変更",
        )
        log.refresh_from_db()

        self.assertNotIn("sk-thisisasecretvalue", log.detail)
        self.assertIn("[REDACTED]", log.detail)

    def test_対象欄もマスクされる(self):
        log = OperationLog.objects.create(
            tenant=self.tenant,
            action="接続確認",
            target="sk-anothersecretvalue",
        )
        log.refresh_from_db()

        self.assertEqual(log.target, "[REDACTED]")


class FeedbackMaskingTests(TestCase):
    def test_コメントがマスクされる(self):
        tenant = Tenant.objects.create(code="acme", name="ACME")
        feedback = Feedback.objects.create(
            tenant=tenant,
            rating=Feedback.Rating.GOOD,
            comment="token: abcdef123456 を貼ってしまいました",
        )
        feedback.refresh_from_db()

        self.assertNotIn("abcdef123456", feedback.comment)
