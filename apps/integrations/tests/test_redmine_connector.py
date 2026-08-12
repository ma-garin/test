"""Redmine コネクタのテスト。

外部ネットワークへは一切出ない。実 API 経路は `_http` を差し替えて検証する。

ここで守りたいのは 3 つ。
- モックが決定的であること（毎回同じ fingerprint。でないと冪等な取込が成立しない）
- ページングを最後まで辿ること（取りこぼしは「同期成功なのにデータが無い」になる）
- API キーが例外文言へ漏れないこと
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

from django.test import SimpleTestCase

from apps.integrations.models import Connection
from apps.integrations.services.connectors import redmine
from apps.integrations.services.connectors.base import ConnectorError

SECRET_KEY_VALUE = "rm-super-secret-token-123456"
ENV_NAME = "REDMINE_API_KEY_FOR_TEST"


class FakeResponse:
    """requests.Response の最小の代替。"""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload


class FakeHttp:
    """`requests` モジュールの代わり。呼び出し内容を記録して検証に使う。"""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url: str, headers: dict, params: dict, timeout: Any) -> FakeResponse:
        self.calls.append(
            {"url": url, "headers": headers, "params": dict(params), "timeout": timeout}
        )

        if not self._responses:
            raise AssertionError("想定より多く HTTP 呼び出しが発生した")

        return self._responses.pop(0)


def make_connection(*, mode: str = Connection.Mode.MOCK, **overrides) -> Connection:
    """DB に保存しない Connection。コネクタは永続化に依存しない。"""

    values = {
        "provider": "redmine",
        "name": "テスト用 Redmine",
        "base_url": "https://redmine.example.com/",
        "credential_env": ENV_NAME,
        "mode": mode,
        "config": {"project_identifier": "pmo"},
    }
    values.update(overrides)

    return Connection(**values)


class RedmineMockModeTests(SimpleTestCase):
    def setUp(self) -> None:
        self.connector = redmine.RedmineConnector(make_connection())

    def test_モックは資格情報なしで取り込める(self):
        issues = list(self.connector.fetch_issues())

        self.assertEqual(len(issues), 10)

    def test_モックは決定的で毎回同じ内容を返す(self):
        first = [(i.external_id, i.fingerprint) for i in self.connector.fetch_issues()]
        second = [(i.external_id, i.fingerprint) for i in self.connector.fetch_issues()]

        self.assertEqual(first, second)

    def test_外部IDが重複しない(self):
        ids = [issue.external_id for issue in self.connector.fetch_issues()]

        self.assertEqual(len(ids), len(set(ids)))

    def test_トラッカーがバグ機能サポートを含む(self):
        # Redmine はトラッカーで種別を分けるので、モックも 3 種を混ぜて実務に近づける。
        labels = {label for issue in self.connector.fetch_issues() for label in issue.labels}

        self.assertTrue({"バグ", "機能", "サポート"} <= labels)

    def test_期限超過と進捗のばらつきがある(self):
        issues = list(self.connector.fetch_issues())
        base = date(2026, 7, 1)
        overdue = [i for i in issues if i.due_date and i.due_date < base]
        ratios = {label for i in issues for label in i.labels if label.startswith("進捗")}

        self.assertGreaterEqual(len(overdue), 3)
        self.assertGreaterEqual(len(ratios), 5)

    def test_モックのcheckは成功する(self):
        status = self.connector.check()

        self.assertTrue(status.ok)

    def test_モックでもURLとキーが埋まる(self):
        issue = next(iter(self.connector.fetch_issues()))

        self.assertTrue(issue.url.startswith("https://redmine.example.com/issues/"))
        self.assertTrue(issue.key.startswith("#"))


class RedmineNormalizationTests(SimpleTestCase):
    def test_RedmineのJSONがExternalIssueへ写る(self):
        connector = redmine.RedmineConnector(make_connection())
        issue = connector._to_issue(
            {
                "id": 2001,
                "subject": "結合試験の環境が確保できない",
                "description": "検証環境の空きが無く着手できない。",
                "tracker": {"id": 1, "name": "バグ"},
                "status": {"id": 2, "name": "進行中"},
                "priority": {"id": 5, "name": "急いで"},
                "assigned_to": {"id": 9, "name": "佐藤 健一"},
                "category": {"id": 1, "name": "環境"},
                "fixed_version": {"id": 2, "name": "v2.0"},
                "due_date": "2026-08-01",
                "done_ratio": 30,
                "updated_on": "2026-07-20T09:30:00Z",
            }
        )

        self.assertEqual(issue.external_id, "2001")
        self.assertEqual(issue.key, "#2001")
        self.assertEqual(issue.title, "結合試験の環境が確保できない")
        self.assertEqual(issue.status, "進行中")
        self.assertEqual(issue.priority, "急いで")
        self.assertEqual(issue.assignee, "佐藤 健一")
        self.assertEqual(issue.due_date, date(2026, 8, 1))
        self.assertEqual(issue.url, "https://redmine.example.com/issues/2001")
        self.assertEqual(issue.labels, ("バグ", "環境", "v2.0", "進捗30%"))
        self.assertIsNotNone(issue.updated_at)
        self.assertEqual(issue.raw["id"], 2001)

    def test_欠けたフィールドがあっても落ちない(self):
        connector = redmine.RedmineConnector(make_connection())
        issue = connector._to_issue({"id": 3001, "subject": "担当未定の課題"})

        self.assertEqual(issue.assignee, "")
        self.assertIsNone(issue.due_date)
        self.assertIsNone(issue.updated_at)

    def test_壊れた日付は取込を止めない(self):
        connector = redmine.RedmineConnector(make_connection())
        issue = connector._to_issue({"id": 3002, "subject": "x", "due_date": "2026/13/45"})

        self.assertIsNone(issue.due_date)


class RedmineLiveModeTests(SimpleTestCase):
    def use_http(self, fake: FakeHttp) -> None:
        """`_http` を差し替える。実ネットワークへは出ない。"""

        original = redmine._http
        redmine._http = lambda: fake
        self.addCleanup(lambda: setattr(redmine, "_http", original))

    def use_credential(self) -> None:
        """環境変数にだけ API キーを置く。DB にも設定にも値は持たせない。"""

        os.environ[ENV_NAME] = SECRET_KEY_VALUE
        self.addCleanup(lambda: os.environ.pop(ENV_NAME, None))

    def make_live(self, **overrides) -> redmine.RedmineConnector:
        connection = make_connection(mode=Connection.Mode.LIVE, **overrides)

        return redmine.RedmineConnector(connection)

    def test_資格情報が無ければConnectorErrorになる(self):
        self.use_http(FakeHttp([]))
        connector = self.make_live()

        # 環境変数を用意しない＝キーが解決できない状態。
        with self.assertRaises(ConnectorError) as ctx:
            list(connector.fetch_issues())

        self.assertIn(ENV_NAME, str(ctx.exception))

    def test_認証失敗の文言にAPIキーが含まれない(self):
        self.use_credential()
        self.use_http(FakeHttp([FakeResponse({}, status_code=401)]))

        with self.assertRaises(ConnectorError) as ctx:
            list(self.make_live().fetch_issues())

        message = str(ctx.exception)

        self.assertNotIn(SECRET_KEY_VALUE, message)
        self.assertIn(ENV_NAME, message)

    def test_ページングで全件を取得する(self):
        self.use_credential()

        page1 = [{"id": n, "subject": f"課題{n}"} for n in range(1, 101)]
        page2 = [{"id": n, "subject": f"課題{n}"} for n in range(101, 121)]
        fake = FakeHttp(
            [
                FakeResponse({"issues": page1, "total_count": 120, "offset": 0, "limit": 100}),
                FakeResponse({"issues": page2, "total_count": 120, "offset": 100, "limit": 100}),
            ]
        )
        self.use_http(fake)

        issues = list(self.make_live().fetch_issues())

        self.assertEqual(len(issues), 120)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[0]["params"]["offset"], 0)
        self.assertEqual(fake.calls[1]["params"]["offset"], 100)
        self.assertEqual(issues[-1].external_id, "120")

    def test_空ページが返れば打ち切る(self):
        self.use_credential()

        # total_count が過大でも、空ページで止まらないと無限ループになる。
        fake = FakeHttp(
            [
                FakeResponse({"issues": [{"id": 1, "subject": "a"}], "total_count": 999}),
                FakeResponse({"issues": [], "total_count": 999}),
            ]
        )
        self.use_http(fake)

        issues = list(self.make_live().fetch_issues())

        self.assertEqual(len(issues), 1)
        self.assertEqual(len(fake.calls), 2)

    def test_リクエストにタイムアウトとAPIキーヘッダが載る(self):
        self.use_credential()

        fake = FakeHttp([FakeResponse({"issues": [], "total_count": 0})])
        self.use_http(fake)

        list(self.make_live().fetch_issues())
        call = fake.calls[0]

        self.assertEqual(call["timeout"], redmine.HTTP_TIMEOUT)
        self.assertEqual(call["headers"]["X-Redmine-API-Key"], SECRET_KEY_VALUE)
        self.assertNotIn("key", call["params"])
        self.assertEqual(call["url"], "https://redmine.example.com/issues.json")
        self.assertEqual(call["params"]["project_id"], "pmo")
        self.assertEqual(call["params"]["sort"], "id")

    def test_ページサイズは100へ丸められる(self):
        self.use_credential()

        fake = FakeHttp([FakeResponse({"issues": [], "total_count": 0})])
        self.use_http(fake)

        connector = self.make_live(config={"project_identifier": "pmo", "limit": 500})
        list(connector.fetch_issues())

        self.assertEqual(fake.calls[0]["params"]["limit"], 100)

    def test_status_idを指定すると絞り込みが渡る(self):
        self.use_credential()

        fake = FakeHttp([FakeResponse({"issues": [], "total_count": 0})])
        self.use_http(fake)

        connector = self.make_live(config={"project_identifier": "pmo", "status_id": "*"})
        list(connector.fetch_issues())

        self.assertEqual(fake.calls[0]["params"]["status_id"], "*")

    def test_プロジェクト識別子が無ければ止まる(self):
        self.use_credential()

        self.use_http(FakeHttp([]))
        connector = self.make_live(config={})

        with self.assertRaises(ConnectorError) as ctx:
            list(connector.fetch_issues())

        self.assertIn("project_identifier", str(ctx.exception))

    def test_通信例外は日本語のConnectorErrorになる(self):
        self.use_credential()

        class Broken:
            def get(self, *args, **kwargs):
                raise OSError("connection reset")

        self.use_http(Broken())

        with self.assertRaises(ConnectorError) as ctx:
            list(self.make_live().fetch_issues())

        message = str(ctx.exception)

        self.assertIn("接続できませんでした", message)
        self.assertNotIn(SECRET_KEY_VALUE, message)

    def test_LIVEのcheckは接続先ユーザー名を返す(self):
        self.use_credential()

        fake = FakeHttp(
            [
                FakeResponse(
                    {"user": {"id": 3, "login": "pmo_admin", "firstname": "花子", "lastname": "山田"}}
                )
            ]
        )
        self.use_http(fake)

        status = self.make_live().check()

        self.assertTrue(status.ok)
        self.assertIn("山田 花子", status.message)
        self.assertEqual(fake.calls[0]["url"], "https://redmine.example.com/users/current.json")
