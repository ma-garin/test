"""Confluence コネクタと文書取込の検証。

守るべき性質は 5 つ。

1. モックが決定的であること（デモと自動テストが同じ結果を見る）
2. fingerprint が「内容が変わったときだけ」変わること（冪等な取込の前提）
3. 資格情報が例外文言へ漏れないこと
4. Confluence の JSON が ExternalPage へ正しく写ること
5. 取込が冪等であること（2 回流しても Document が増えない）

**実通信は一切しない。** LIVE 経路は `_load_requests` を差し替えて検証する。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.documents.models import Document, DocumentPage
from apps.integrations.models import Connection, SyncedRecord, SyncJob
from apps.integrations.services.confluence_sync import run_confluence_pull
from apps.integrations.services.connectors import confluence as confluence_module
from apps.integrations.services.connectors import get_connector
from apps.integrations.services.connectors.base import ConnectorError
from apps.integrations.services.connectors.confluence import (
    MOCK_PAGE_SEEDS,
    ConfluenceConnector,
    ExternalPage,
    _storage_to_text,
)
from apps.projects.models import Project

#: 例外・メッセージへ絶対に現れてはいけない値。
SECRET_TOKEN = "super-secret-confluence-token-000"
TOKEN_ENV = "CONFLUENCE_API_TOKEN_FOR_TEST"


class FakeResponse:
    """requests.Response の最小限の代役。"""

    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class FakeRequests:
    """`requests` モジュールの代役。呼び出し内容を記録する。"""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})

        return self._responses.pop(0) if self._responses else FakeResponse(200, {"results": []})


def _connection(tenant, *, project=None, **overrides) -> Connection:
    payload = {
        "tenant": tenant,
        "project": project,
        # Provider にはまだ CONFLUENCE が無いため素の文字列で持つ（実装側の TODO と対）。
        "provider": "confluence",
        "name": "Confluence（モック）",
        "base_url": "https://example.atlassian.net",
        "mode": Connection.Mode.MOCK,
        "config": {"space_key": "PMO", "email": "pmo@example.com"},
    }
    payload.update(overrides)

    return Connection.objects.create(**payload)


class ConfluenceMockTests(TestCase):
    """モックモードは API キー無しで完結する。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="cf1", name="テナントCF1")
        self.connection = _connection(self.tenant)

    def test_モックは8件を決定的に返す(self):
        first = list(ConfluenceConnector(self.connection).fetch_pages())
        second = list(ConfluenceConnector(self.connection).fetch_pages())

        self.assertEqual(len(first), 8)
        self.assertEqual(len(MOCK_PAGE_SEEDS), 8)
        self.assertEqual(
            [page.page_id for page in first], [page.page_id for page in second]
        )
        self.assertEqual(
            [page.fingerprint for page in first], [page.fingerprint for page in second]
        )

    def test_モックは日本語の本文とURLを持つ(self):
        pages = list(ConfluenceConnector(self.connection).fetch_pages())

        for page in pages:
            self.assertTrue(page.title)
            self.assertTrue(page.body_text)
            self.assertIn("/wiki/spaces/PMO/pages/", page.url)
            self.assertEqual(page.space_key, "PMO")

    def test_疎通確認はモックだと必ず成功する(self):
        status = ConfluenceConnector(self.connection).check()

        self.assertTrue(status.ok)
        self.assertEqual(status.detail["mode"], "mock")

    def test_get_connectorがConfluenceコネクタを返す(self):
        self.assertIsInstance(get_connector(self.connection), ConfluenceConnector)

    def test_fingerprintは本文が変わったときだけ変わる(self):
        base = ExternalPage(page_id="1", title="設計書", body_text="本文", space_key="PMO")

        self.assertEqual(base.fingerprint, ExternalPage(**{**base.__dict__, "version": 9}).fingerprint)
        self.assertNotEqual(
            base.fingerprint, ExternalPage(**{**base.__dict__, "body_text": "本文（改訂）"}).fingerprint
        )


class ConfluenceStorageFormatTests(TestCase):
    """storage 形式（XHTML）を文章へ落とせること。"""

    def test_ブロック要素は改行になりタグは消える(self):
        text = _storage_to_text(
            "<h2>帳票設計</h2><p>A4縦で出力する。</p>"
            "<ul><li>明細は30行</li><li>合計欄を再掲</li></ul>"
            "<style>.x{}</style><p>&quot;税区分&quot;は未反映</p>"
        )

        self.assertEqual(
            text,
            "帳票設計\nA4縦で出力する。\n明細は30行\n合計欄を再掲\n\"税区分\"は未反映",
        )

    def test_空文字は空文字のまま(self):
        self.assertEqual(_storage_to_text(""), "")


class ConfluenceLiveTests(TestCase):
    """LIVE 経路。実通信はせず、`_load_requests` を差し替える。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="cf2", name="テナントCF2")
        self.connection = _connection(
            self.tenant,
            mode=Connection.Mode.LIVE,
            credential_env=TOKEN_ENV,
            name="Confluence（実API）",
        )

    def test_資格情報が無ければ通信せずに失敗する(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop(TOKEN_ENV, None)

            with mock.patch.object(confluence_module, "_load_requests") as loader:
                with self.assertRaises(ConnectorError) as raised:
                    list(ConfluenceConnector(self.connection).fetch_pages())

        self.assertIn(TOKEN_ENV, str(raised.exception))
        self.assertNotIn(SECRET_TOKEN, str(raised.exception))
        loader.assert_not_called()

    def test_スペースキーが無ければ失敗する(self):
        self.connection.config = {"email": "pmo@example.com"}

        with self.assertRaises(ConnectorError) as raised:
            list(ConfluenceConnector(self.connection).fetch_pages())

        self.assertIn("space_key", str(raised.exception))

    def test_認証失敗の文言にトークンが含まれない(self):
        fake = FakeRequests([FakeResponse(401, {"message": SECRET_TOKEN})])

        with mock.patch.dict("os.environ", {TOKEN_ENV: SECRET_TOKEN}):
            with mock.patch.object(confluence_module, "_load_requests", return_value=fake):
                with self.assertRaises(ConnectorError) as raised:
                    list(ConfluenceConnector(self.connection).fetch_pages())

        message = str(raised.exception)
        self.assertIn("認証に失敗", message)
        self.assertNotIn(SECRET_TOKEN, message)

    def test_応答をExternalPageへ写す(self):
        payload = {
            "results": [
                {
                    "id": "998877",
                    "title": "基本設計書",
                    "space": {"key": "PMO"},
                    "version": {"number": 4, "when": "2026-07-20T10:12:33.000+09:00"},
                    "body": {"storage": {"value": "<p>設計の本文</p>"}},
                    "metadata": {"labels": {"results": [{"name": "設計書"}]}},
                    "_links": {"webui": "/spaces/PMO/pages/998877"},
                }
            ]
        }
        fake = FakeRequests([FakeResponse(200, payload)])

        with mock.patch.dict("os.environ", {TOKEN_ENV: SECRET_TOKEN}):
            with mock.patch.object(confluence_module, "_load_requests", return_value=fake):
                pages = list(ConfluenceConnector(self.connection).fetch_pages())

        self.assertEqual(len(pages), 1)
        page = pages[0]
        self.assertEqual(page.page_id, "998877")
        self.assertEqual(page.body_text, "設計の本文")
        self.assertEqual(page.version, 4)
        self.assertEqual(page.labels, ("設計書",))
        self.assertEqual(page.url, "https://example.atlassian.net/spaces/PMO/pages/998877")
        self.assertEqual(fake.calls[0]["params"]["spaceKey"], "PMO")

    def test_CQLを指定してもスペース条件が必ず入る(self):
        self.connection.config = {
            "space_key": "PMO",
            "email": "pmo@example.com",
            "cql": 'label = "設計書"',
        }
        fake = FakeRequests([FakeResponse(200, {"results": []})])

        with mock.patch.dict("os.environ", {TOKEN_ENV: SECRET_TOKEN}):
            with mock.patch.object(confluence_module, "_load_requests", return_value=fake):
                list(ConfluenceConnector(self.connection).fetch_pages())

        self.assertIn("content/search", fake.calls[0]["url"])
        self.assertIn('space = "PMO"', fake.calls[0]["params"]["cql"])


class ConfluenceSyncTests(TestCase):
    """文書台帳への取込。冪等であることが最重要。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="cf3", name="テナントCF3")
        self.project = Project.objects.create(tenant=self.tenant, code="pcf", name="案件CF")
        self.connection = _connection(self.tenant, project=self.project)

    def test_モックのまま端から端まで通る(self):
        job = run_confluence_pull(self.connection)

        self.assertEqual(job.status, SyncJob.Status.SUCCEEDED)
        self.assertEqual(job.created_count, 8)
        self.assertEqual(Document.objects.count(), 8)
        self.assertEqual(DocumentPage.objects.count(), 8)
        self.assertEqual(SyncedRecord.objects.filter(connection=self.connection).count(), 8)

        document = Document.objects.get(title="基本設計書 第3章 帳票設計")
        self.assertEqual(document.tenant, self.tenant)
        self.assertEqual(document.project, self.project)
        self.assertIn("Confluence PMO/500101", document.source_note)
        self.assertTrue(document.pages.first().content)

        self.connection.refresh_from_db()
        self.assertIsNotNone(self.connection.last_synced_at)

    def test_二回流しても文書は増えない(self):
        run_confluence_pull(self.connection)
        second = run_confluence_pull(self.connection)

        self.assertEqual(second.status, SyncJob.Status.SUCCEEDED)
        self.assertEqual(second.created_count, 0)
        self.assertEqual(second.skipped_count, 8)
        self.assertEqual(Document.objects.count(), 8)
        self.assertEqual(DocumentPage.objects.count(), 8)

    def test_本文が変わったら更新される(self):
        run_confluence_pull(self.connection)
        record = SyncedRecord.objects.get(connection=self.connection, external_id="500101")
        stale = record.fingerprint
        record.fingerprint = "changed"
        record.save(update_fields=["fingerprint", "updated_at"])

        job = run_confluence_pull(self.connection)

        self.assertEqual(job.updated_count, 1)
        self.assertEqual(job.skipped_count, 7)
        self.assertEqual(Document.objects.count(), 8)
        record.refresh_from_db()
        self.assertEqual(record.fingerprint, stale)

    def test_文書が消えていれば作り直す(self):
        run_confluence_pull(self.connection)
        record = SyncedRecord.objects.get(connection=self.connection, external_id="500101")
        Document.objects.filter(pk=record.object_id).delete()

        job = run_confluence_pull(self.connection)

        self.assertEqual(job.created_count, 1)
        self.assertEqual(Document.objects.count(), 8)

    def test_無効な接続は履歴を残して失敗する(self):
        self.connection.is_active = False
        self.connection.save(update_fields=["is_active"])

        job = run_confluence_pull(self.connection)

        self.assertEqual(job.status, SyncJob.Status.FAILED)
        self.assertIn("無効", job.message)
        self.assertEqual(Document.objects.count(), 0)

    def test_Confluence以外の接続は受け付けない(self):
        other = Connection.objects.create(
            tenant=self.tenant,
            provider="jira",
            name="Jira",
            mode=Connection.Mode.MOCK,
            config={"project_key": "PMO"},
        )

        job = run_confluence_pull(other)

        self.assertEqual(job.status, SyncJob.Status.FAILED)
        self.assertIn("Confluence", job.message)

    def test_取得時の失敗も履歴に残る(self):
        live = _connection(
            self.tenant,
            project=self.project,
            name="Confluence（実API・鍵なし）",
            mode=Connection.Mode.LIVE,
            credential_env=TOKEN_ENV,
        )

        import os

        os.environ.pop(TOKEN_ENV, None)
        job = run_confluence_pull(live)

        self.assertEqual(job.status, SyncJob.Status.FAILED)
        self.assertIn(TOKEN_ENV, job.message)
        self.assertNotIn(SECRET_TOKEN, job.message)


class ConfluenceReferenceTimeTests(TestCase):
    """基準時刻を固定すれば更新日時まで決定的になる。"""

    def test_更新日時は基準時刻から導かれる(self):
        tenant = Tenant.objects.create(code="cf4", name="テナントCF4")
        connection = _connection(tenant)
        anchor = timezone.make_aware(datetime(2026, 7, 20, 12, 0))

        pages = list(ConfluenceConnector(connection, reference_time=anchor).fetch_pages())

        self.assertEqual(pages[0].updated_at, anchor - timedelta(hours=6))
