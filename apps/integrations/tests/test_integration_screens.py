"""連携画面の情報設計（UXP-32 / UXP-33 / UXP-46）。

見ているのは「運用者が画面だけで次の行動を決められるか」である。
接続が読み取り専用であること、最後に成功したのはいつか、失敗した理由は何か、
異常な接続が埋もれていないか、そして資格情報の値が画面に出ていないか。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.integrations.models import Connection, Provider, SyncJob
from apps.projects.models import Project


class IntegrationScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="t1", name="テナント1")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.admin = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.healthy = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.JIRA,
            name="正常なJira",
            mode=Connection.Mode.MOCK,
        )
        self.broken = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.REDMINE,
            name="止まったRedmine",
            mode=Connection.Mode.MOCK,
        )
        self.now = timezone.now()

    def _succeed(self, connection: Connection, *, hours_ago: float = 1) -> SyncJob:
        moment = self.now - timedelta(hours=hours_ago)
        connection.last_synced_at = moment
        connection.save(update_fields=["last_synced_at"])

        return SyncJob.objects.create(
            connection=connection,
            status=SyncJob.Status.SUCCEEDED,
            started_at=moment,
            finished_at=moment,
            created_count=3,
            message="3 件を取り込みました",
        )

    # ── UXP-32: 一覧 ────────────────────────────────────────

    def test_一覧に最終成功時刻と失敗理由が出る(self) -> None:
        self._succeed(self.healthy)
        SyncJob.objects.create(
            connection=self.healthy,
            status=SyncJob.Status.FAILED,
            finished_at=self.now,
            failed_count=2,
            message="接続先が応答しません（タイムアウト）",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("integrations:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "最終成功")
        self.assertContains(response, "失敗理由")
        self.assertContains(response, "接続先が応答しません（タイムアウト）")

        row = next(r for r in response.context["rows"] if r.connection == self.healthy)
        self.assertIsNotNone(row.last_success_at)

    def test_一覧は同期対象と読み取り専用であることを行に出す(self) -> None:
        self.client.force_login(self.admin)

        response = self.client.get(reverse("integrations:list"))

        self.assertContains(response, "同期対象")
        self.assertContains(response, "課題・チケット（外部 → 内部）")
        self.assertContains(response, "外部へ書き込みません（読み取り専用）")
        # 同期実行の近くに、対象・実行結果・取り消し可否を書く。
        self.assertContains(response, "取り消し: できません")

    # ── UXP-33: 同期の稼働状況 ────────────────────────────

    def test_パイプラインは異常のある接続を先頭に固定する(self) -> None:
        # 正常な接続（直近で成功）と、一度も成功していない接続を並べる。
        # 既定の並び（連携先→名前）では jira が先に来るため、順序が入れ替わることを見る。
        self._succeed(self.healthy)
        self.client.force_login(self.admin)

        response = self.client.get(reverse("integrations:pipeline"))

        self.assertEqual(response.status_code, 200)
        rows = response.context["rows"]
        self.assertEqual(rows[0].connection, self.broken)
        self.assertEqual(rows[-1].connection, self.healthy)
        self.assertTrue(rows[0].needs_attention)
        self.assertFalse(rows[-1].needs_attention)
        self.assertContains(response, "今すぐ確認する接続")
        self.assertContains(response, "一度も同期に成功していません")
        # 各行から接続設定・同期履歴へ行ける。
        self.assertContains(response, reverse("integrations:edit", args=[self.broken.pk]))
        self.assertContains(response, reverse("integrations:job_list"))

    def test_パイプラインは正常時に監視対象数と確認頻度を出す(self) -> None:
        self._succeed(self.healthy)
        self._succeed(self.broken)
        self.client.force_login(self.admin)

        response = self.client.get(reverse("integrations:pipeline"))

        self.assertEqual(response.context["attention_rows"], [])
        self.assertContains(response, "監視対象 2 件はすべて正常です")
        self.assertContains(response, "次に確認する目安は 24 時間ごとです")

    def test_パイプラインは0件でも監視対象数と確認頻度を出す(self) -> None:
        Connection.objects.all().delete()
        self.client.force_login(self.admin)

        response = self.client.get(reverse("integrations:pipeline"))

        self.assertContains(response, "監視対象の接続は 0 件です")
        self.assertContains(response, "24 時間ごとに確認する鮮度")

    # ── UXP-46: 接続の作成・編集 ────────────────────────────

    def test_接続フォームに4ステップの見出しが出る(self) -> None:
        self.client.force_login(self.admin)

        response = self.client.get(reverse("integrations:create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ステップ1: 接続先")
        self.assertContains(response, "ステップ2: 認証の参照名")
        self.assertContains(response, "ステップ3: 取込範囲")
        self.assertContains(response, "ステップ4: 疎通確認")
        # 新規作成は 1 番目が現在地。既存の接続は疎通確認が残っている。
        self.assertEqual(response.context["current_step"], 1)

        edit = self.client.get(reverse("integrations:edit", args=[self.healthy.pk]))
        self.assertEqual(edit.context["current_step"], 4)

    def test_接続フォームは認証の値そのものを画面に出さない(self) -> None:
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("integrations:create"),
            {
                "project": str(self.project.pk),
                "provider": Provider.JIRA,
                "name": "追加する接続",
                "base_url": "https://example.invalid",
                "credential_env": "ATATT-secret-value-do-not-show",
                "mode": Connection.Mode.MOCK,
                "config": "{}",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Connection.objects.filter(name="追加する接続").exists())
        self.assertNotContains(response, "ATATT-secret-value-do-not-show")
        self.assertContains(response, "参照名（環境変数の名前）だけ")
        # 入力エラーのあるステップ（認証の参照名）へ現在地が戻る。
        self.assertEqual(response.context["current_step"], 2)


class IntegrationListInformationTests(TestCase):
    """UXP-32 で列見出しを組み替えたが、判断に要る値は落とさない。

    見出しの言い換えは設計判断だが、値が消えるのは回帰である。
    列名ではなく「値が出ているか」で固定する。
    """

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="admin-user",
            email="admin@example.com",
            password="pw",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.connection = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.JIRA,
            name="Jira（読み取り専用）",
            credential_env="JIRA_API_TOKEN",
        )
        self.client.force_login(self.user)

    def test_取込先の案件が分かる(self) -> None:
        response = self.client.get(reverse("integrations:list"))
        self.assertContains(response, self.project.code)

    def test_資格情報は環境変数名だけを出す(self) -> None:
        response = self.client.get(reverse("integrations:list"))
        self.assertContains(response, "JIRA_API_TOKEN")

    def test_モックか実APIかが分かる(self) -> None:
        response = self.client.get(reverse("integrations:list"))
        self.assertContains(response, "モック")

    def test_接続の表示名と連携先が分かる(self) -> None:
        response = self.client.get(reverse("integrations:list"))
        self.assertContains(response, "Jira（読み取り専用）")
        self.assertContains(response, self.connection.get_provider_display())
