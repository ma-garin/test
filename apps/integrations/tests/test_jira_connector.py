"""Jira コネクタのテスト。

このコネクタが守るべき性質は 4 つ。

1. モックが決定的であること（デモと自動テストが同じ結果を見る）
2. fingerprint が「内容が変わったときだけ」変わること（冪等な取込の前提）
3. 資格情報が例外文言へ漏れないこと
4. Jira の JSON が ExternalIssue へ正しく写ること

**実通信は一切しない。** LIVE 経路は `_load_requests` を差し替えて検証する。
"""

from __future__ import annotations

import os
from datetime import date
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.integrations.models import Connection, Provider
from apps.integrations.services.connectors import jira as jira_module
from apps.integrations.services.connectors.base import ConnectorError
from apps.integrations.services.connectors.jira import JiraConnector

#: 例外・メッセージへ絶対に現れてはいけない値。
SECRET_TOKEN = "super-secret-jira-token-000"
TOKEN_ENV = "JIRA_API_TOKEN_FOR_TEST"


class FakeResponse:
    """requests.Response の最小限の代役。"""

    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload

        return self._payload


class FakeRequests:
    """`requests` モジュールの代役。呼び出し内容を記録する。"""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})

        return self._responses.pop(0) if self._responses else FakeResponse(200, {})


class ExplodingRequests:
    """通信例外を再現する。requests の例外階層に依存しない形で名前だけ真似る。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def get(self, url: str, **kwargs) -> FakeResponse:
        raise self._exc


class ConnectTimeout(Exception):
    pass


class ConnectionError_(Exception):
    # requests.exceptions.ConnectionError と同じ名前で判定させる
    pass


ConnectionError_.__name__ = "ConnectionError"


class JiraConnectorTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="t1", name="テナント1")

    def _connection(self, **overrides) -> Connection:
        defaults = {
            "tenant": self.tenant,
            "provider": Provider.JIRA,
            "name": "Jira接続",
            "base_url": "https://example.atlassian.net",
            "credential_env": TOKEN_ENV,
            "mode": Connection.Mode.MOCK,
            "config": {"project_key": "PMO", "email": "pmo@example.com"},
        }
        defaults.update(overrides)

        return Connection.objects.create(**defaults)


class MockModeTests(JiraConnectorTestBase):
    def test_モックは資格情報なしで課題を返す(self):
        connector = JiraConnector(self._connection())

        issues = list(connector.fetch_issues())

        self.assertEqual(len(issues), 10)
        self.assertTrue(all(issue.key.startswith("PMO-") for issue in issues))
        self.assertTrue(all(issue.title for issue in issues))

    def test_モックは何度呼んでも同じ結果を返す(self):
        connector = JiraConnector(self._connection(), reference_date=date(2026, 7, 1))

        first = list(connector.fetch_issues())
        second = list(connector.fetch_issues())

        self.assertEqual(
            [(i.external_id, i.fingerprint, i.due_date) for i in first],
            [(i.external_id, i.fingerprint, i.due_date) for i in second],
        )

    def test_モックには期限超過の未完了課題が含まれる(self):
        reference = date(2026, 7, 1)
        connector = JiraConnector(self._connection(), reference_date=reference)

        overdue = [
            issue
            for issue in connector.fetch_issues()
            if issue.due_date and issue.due_date < reference and issue.status != "完了"
        ]

        self.assertGreaterEqual(len(overdue), 3)

    def test_モックは状態と優先度と担当にばらつきを持つ(self):
        connector = JiraConnector(self._connection())
        issues = list(connector.fetch_issues())

        self.assertGreaterEqual(len({i.status for i in issues}), 4)
        self.assertGreaterEqual(len({i.priority for i in issues}), 4)
        # 担当未割当（実務で普通に起きる）も混ざっていること
        self.assertIn("", {i.assignee for i in issues})

    def test_モックのURLは接続のベースURLから組み立てられる(self):
        connector = JiraConnector(self._connection(base_url="https://acme.atlassian.net"))

        first = next(iter(connector.fetch_issues()))

        self.assertEqual(first.url, "https://acme.atlassian.net/browse/PMO-101")

    def test_モードがモックなら疎通確認は常に成功する(self):
        status = JiraConnector(self._connection(credential_env="")).check()

        self.assertTrue(status.ok)
        self.assertIn("モック", status.message)


class FingerprintTests(JiraConnectorTestBase):
    """冪等な取込の前提。ここが崩れると毎回全件更新になる。"""

    def test_内容が変わらなければ指紋も変わらない(self):
        connection = self._connection()
        a = list(JiraConnector(connection, reference_date=date(2026, 7, 1)).fetch_issues())
        b = list(JiraConnector(connection, reference_date=date(2026, 7, 1)).fetch_issues())

        self.assertEqual([i.fingerprint for i in a], [i.fingerprint for i in b])

    def test_内容が変わると指紋も変わる(self):
        original = next(iter(JiraConnector(self._connection()).fetch_issues()))

        from dataclasses import replace

        self.assertNotEqual(original.fingerprint, replace(original, status="完了").fingerprint)
        self.assertNotEqual(original.fingerprint, replace(original, title="別の件名").fingerprint)
        self.assertNotEqual(
            original.fingerprint, replace(original, labels=("要件定義",)).fingerprint
        )

    def test_更新日時だけが動いても指紋は変わらない(self):
        from dataclasses import replace
        from datetime import timedelta

        original = next(iter(JiraConnector(self._connection()).fetch_issues()))
        moved = replace(original, updated_at=(original.updated_at or None))

        if original.updated_at is not None:
            moved = replace(original, updated_at=original.updated_at + timedelta(days=1))

        self.assertEqual(original.fingerprint, moved.fingerprint)


class CredentialTests(JiraConnectorTestBase):
    def test_資格情報が無い状態でLIVEを呼ぶとConnectorErrorになる(self):
        connection = self._connection(mode=Connection.Mode.LIVE)

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(TOKEN_ENV, None)

            with self.assertRaises(ConnectorError) as ctx:
                list(JiraConnector(connection).fetch_issues())

        message = str(ctx.exception)
        self.assertIn("資格情報", message)
        # 環境変数の「名前」は出してよいが、値は絶対に出さない
        self.assertIn(TOKEN_ENV, message)
        self.assertNotIn(SECRET_TOKEN, message)

    def test_認証エラーの文言にトークンの値が含まれない(self):
        connection = self._connection(mode=Connection.Mode.LIVE)
        fake = FakeRequests([FakeResponse(401, {"errorMessages": ["Unauthorized"]})])

        with mock.patch.dict(os.environ, {TOKEN_ENV: SECRET_TOKEN}):
            with mock.patch.object(jira_module, "_load_requests", return_value=fake):
                with self.assertRaises(ConnectorError) as ctx:
                    list(JiraConnector(connection).fetch_issues())

        message = str(ctx.exception)
        self.assertNotIn(SECRET_TOKEN, message)
        self.assertIn("認証", message)

    def test_疎通確認の結果にトークンの値が含まれない(self):
        connection = self._connection(mode=Connection.Mode.LIVE)
        fake = FakeRequests([FakeResponse(200, {"displayName": "PMO 太郎"})])

        with mock.patch.dict(os.environ, {TOKEN_ENV: SECRET_TOKEN}):
            with mock.patch.object(jira_module, "_load_requests", return_value=fake):
                status = JiraConnector(connection).check()

        self.assertTrue(status.ok)
        self.assertIn("PMO 太郎", status.message)
        self.assertNotIn(SECRET_TOKEN, status.message)
        self.assertNotIn(SECRET_TOKEN, str(status.detail))
        # 疎通確認は /myself を叩く
        self.assertTrue(fake.calls[0]["url"].endswith("/rest/api/3/myself"))


class LiveFetchTests(JiraConnectorTestBase):
    """実通信はしない。requests の代役へ Jira の JSON を流し込んで正規化を検証する。"""

    JIRA_PAYLOAD = {
        "total": 1,
        "issues": [
            {
                "id": "10101",
                "key": "PMO-201",
                "fields": {
                    "summary": "受入テストの環境が確保できていない",
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": "業務部門の"},
                                    {"type": "text", "text": "端末が不足している。"},
                                ],
                            },
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "調達に2週間かかる見込み。"}],
                            },
                        ],
                    },
                    "status": {"name": "対応中"},
                    "priority": {"name": "High"},
                    "assignee": {"displayName": "佐藤 健"},
                    "duedate": "2026-08-31",
                    "labels": ["受入", "環境"],
                    "updated": "2026-07-20T10:12:33.000+0900",
                },
            }
        ],
    }

    def _fetch(self, payload: object, *, connection=None) -> tuple[list, FakeRequests]:
        connection = connection or self._connection(mode=Connection.Mode.LIVE)
        fake = FakeRequests([FakeResponse(200, payload)])

        with mock.patch.dict(os.environ, {TOKEN_ENV: SECRET_TOKEN}):
            with mock.patch.object(jira_module, "_load_requests", return_value=fake):
                issues = list(JiraConnector(connection).fetch_issues())

        return issues, fake

    def test_JiraのJSONがExternalIssueへ正規化される(self):
        issues, _ = self._fetch(self.JIRA_PAYLOAD)

        self.assertEqual(len(issues), 1)
        issue = issues[0]

        self.assertEqual(issue.external_id, "10101")
        self.assertEqual(issue.key, "PMO-201")
        self.assertEqual(issue.title, "受入テストの環境が確保できていない")
        self.assertEqual(issue.status, "対応中")
        self.assertEqual(issue.priority, "High")
        self.assertEqual(issue.assignee, "佐藤 健")
        self.assertEqual(issue.due_date, date(2026, 8, 31))
        self.assertEqual(issue.labels, ("受入", "環境"))
        self.assertEqual(issue.url, "https://example.atlassian.net/browse/PMO-201")
        self.assertEqual(issue.description, "業務部門の端末が不足している。\n調達に2週間かかる見込み。")
        self.assertIsNotNone(issue.updated_at)
        self.assertEqual(issue.updated_at.year, 2026)
        # 生データを残しておかないと、マッピングの誤りを後から追えない
        self.assertEqual(issue.raw["key"], "PMO-201")

    def test_欠損したフィールドがあっても落ちない(self):
        payload = {
            "total": 1,
            "issues": [{"id": "10102", "key": "PMO-202", "fields": {"summary": "担当未割当の課題"}}],
        }

        issues, _ = self._fetch(payload)
        issue = issues[0]

        self.assertEqual(issue.assignee, "")
        self.assertEqual(issue.status, "")
        self.assertIsNone(issue.due_date)
        self.assertEqual(issue.labels, ())
        self.assertEqual(issue.description, "")

    def test_リクエストにタイムアウトと認証とJQLが設定される(self):
        connection = self._connection(
            mode=Connection.Mode.LIVE,
            config={"project_key": "PMO", "email": "pmo@example.com", "jql": "status != Done"},
        )
        _, fake = self._fetch(self.JIRA_PAYLOAD, connection=connection)
        call = fake.calls[0]

        self.assertTrue(call["url"].endswith("/rest/api/3/search"))
        self.assertEqual(call["auth"], ("pmo@example.com", SECRET_TOKEN))
        # 無限待ちを防ぐため、タイムアウトは必ず入っていること
        self.assertIsNotNone(call["timeout"])
        self.assertEqual(call["params"]["jql"], 'project = "PMO" AND (status != Done) ORDER BY updated DESC')

    def test_プロジェクトキーが未設定なら取込前に止まる(self):
        connection = self._connection(
            mode=Connection.Mode.LIVE, config={"email": "pmo@example.com"}
        )

        with mock.patch.dict(os.environ, {TOKEN_ENV: SECRET_TOKEN}):
            with self.assertRaises(ConnectorError) as ctx:
                list(JiraConnector(connection).fetch_issues())

        self.assertIn("project_key", str(ctx.exception))


class ErrorMessageTests(JiraConnectorTestBase):
    """「401 と出ています」で問い合わせが止まらないよう、原因を区別する。"""

    def _raise_for(self, response: FakeResponse) -> str:
        # 同一テスト内で何度も呼ぶため、接続は 1 つを使い回す（表示名は一意制約がある）
        connection = getattr(self, "_shared", None) or self._connection(mode=Connection.Mode.LIVE)
        self._shared = connection
        fake = FakeRequests([response])

        with mock.patch.dict(os.environ, {TOKEN_ENV: SECRET_TOKEN}):
            with mock.patch.object(jira_module, "_load_requests", return_value=fake):
                with self.assertRaises(ConnectorError) as ctx:
                    list(JiraConnector(connection).fetch_issues())

        return str(ctx.exception)

    def test_認証と権限と対象不明を区別する(self):
        auth = self._raise_for(FakeResponse(401, {}))
        forbidden = self._raise_for(FakeResponse(403, {"errorMessages": ["No permission"]}))
        missing = self._raise_for(
            FakeResponse(400, {"errorMessages": ["The value 'ZZZ' does not exist for the field 'project'."]})
        )
        bad_jql = self._raise_for(FakeResponse(400, {"errorMessages": ["Error in the JQL Query."]}))

        self.assertIn("認証に失敗", auth)
        self.assertIn("権限", forbidden)
        self.assertIn("project_key", missing)
        self.assertIn("jql", bad_jql)
        # それぞれ別の文言であること（同じ文言なら区別できていない）
        self.assertEqual(len({auth, forbidden, missing, bad_jql}), 4)

    def test_タイムアウトと接続不可を日本語で説明する(self):
        connection = self._connection(mode=Connection.Mode.LIVE)

        for exc, expected in (
            (ConnectTimeout("timed out"), "タイムアウト"),
            (ConnectionError_("unreachable"), "接続できません"),
        ):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.dict(os.environ, {TOKEN_ENV: SECRET_TOKEN}):
                    with mock.patch.object(
                        jira_module, "_load_requests", return_value=ExplodingRequests(exc)
                    ):
                        with self.assertRaises(ConnectorError) as ctx:
                            list(JiraConnector(connection).fetch_issues())

                self.assertIn(expected, str(ctx.exception))

    def test_JSONで無い応答でも例外にならず日本語で説明する(self):
        message = self._raise_for(FakeResponse(200, ValueError("not json")))

        self.assertIn("応答", message)

    def test_ベースURLが未設定なら取込前に止まる(self):
        connection = self._connection(mode=Connection.Mode.LIVE, base_url="")

        with mock.patch.dict(os.environ, {TOKEN_ENV: SECRET_TOKEN}):
            with self.assertRaises(ConnectorError) as ctx:
                list(JiraConnector(connection).fetch_issues())

        self.assertIn("ベースURL", str(ctx.exception))


class AdfTests(TestCase):
    """Jira v3 の本文（Atlassian Document Format）をテキストへ落とす。"""

    def test_見出しとリストも行として拾う(self):
        doc = {
            "type": "doc",
            "content": [
                {"type": "heading", "content": [{"type": "text", "text": "背景"}]},
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "環境不足"}]}
                            ],
                        }
                    ],
                },
            ],
        }

        self.assertEqual(jira_module._adf_to_text(doc), "背景\n環境不足")

    def test_文字列やNoneも安全に扱う(self):
        self.assertEqual(jira_module._adf_to_text("素のテキスト"), "素のテキスト")
        self.assertEqual(jira_module._adf_to_text(None), "")
        self.assertEqual(jira_module._adf_to_text(SimpleNamespace()), "")
