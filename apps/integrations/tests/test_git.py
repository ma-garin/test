"""Git コネクタとコミット集計の検証。

守るべき性質は 5 つ。

1. モックが決定的であること（デモと自動テストが同じ結果を見る）
2. 資格情報が例外文言へ漏れないこと
3. GitHub の JSON が ExternalCommit へ正しく写ること
4. 日次集計が期間の外を混ぜないこと
5. 異常検知が「標本が少なければ何も言わない」こと

**実通信は一切しない。** LIVE 経路は `_load_requests` を差し替えて検証する。
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.integrations.models import Connection
from apps.integrations.services.connectors import get_connector
from apps.integrations.services.connectors import git as git_module
from apps.integrations.services.connectors.base import ConnectorError
from apps.integrations.services.connectors.git import (
    MOCK_COMMIT_SEEDS,
    ExternalCommit,
    GitConnector,
)
from apps.integrations.services.git_stats import (
    DailyActivity,
    detect_anomalies,
    summarize_commits,
)

SECRET_TOKEN = "super-secret-github-token-000"
TOKEN_ENV = "GITHUB_TOKEN_FOR_TEST"

#: 集計を決定的にするための基準時刻。
ANCHOR = datetime(2026, 7, 20, 18, 0)


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class FakeRequests:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})

        return self._responses.pop(0) if self._responses else FakeResponse(200, [])


def _connection(tenant, **overrides) -> Connection:
    payload = {
        "tenant": tenant,
        # Provider にはまだ GIT が無いため素の文字列で持つ（実装側の TODO と対）。
        "provider": "git",
        "name": "Git（モック）",
        "mode": Connection.Mode.MOCK,
        "config": {"owner": "example-corp", "repo": "pmo-agent", "branch": "main"},
    }
    payload.update(overrides)

    return Connection.objects.create(**payload)


class GitMockTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="gt1", name="テナントGT1")
        self.connection = _connection(self.tenant)

    def test_モックは10件を決定的に返す(self):
        anchor = timezone.make_aware(ANCHOR)
        first = list(GitConnector(self.connection, reference_time=anchor).fetch_commits())
        second = list(GitConnector(self.connection, reference_time=anchor).fetch_commits())

        self.assertEqual(len(first), 10)
        self.assertEqual(len(MOCK_COMMIT_SEEDS), 10)
        self.assertEqual([c.sha for c in first], [c.sha for c in second])
        self.assertEqual([c.committed_at for c in first], [c.committed_at for c in second])

    def test_モックは変更行数とURLを持つ(self):
        commits = list(GitConnector(self.connection).fetch_commits())

        for commit in commits:
            self.assertTrue(commit.summary)
            self.assertGreater(commit.churn, 0)
            self.assertIn("github.com/example-corp/pmo-agent/commit/", commit.url)

    def test_疎通確認はモックだと必ず成功する(self):
        status = GitConnector(self.connection).check()

        self.assertTrue(status.ok)
        self.assertEqual(status.detail["mode"], "mock")

    def test_get_connectorがGitコネクタを返す(self):
        self.assertIsInstance(get_connector(self.connection), GitConnector)


class GitLiveTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="gt2", name="テナントGT2")
        self.connection = _connection(
            self.tenant,
            name="Git（実API）",
            mode=Connection.Mode.LIVE,
            credential_env=TOKEN_ENV,
        )
        os.environ.pop(TOKEN_ENV, None)

    def test_資格情報が無ければ失敗する(self):
        with mock.patch.object(git_module, "_load_requests", return_value=FakeRequests([])):
            with self.assertRaises(ConnectorError) as raised:
                list(GitConnector(self.connection).fetch_commits())

        self.assertIn(TOKEN_ENV, str(raised.exception))
        self.assertNotIn(SECRET_TOKEN, str(raised.exception))

    def test_リポジトリ未設定なら通信せずに失敗する(self):
        self.connection.config = {}

        with mock.patch.object(git_module, "_load_requests") as loader:
            with self.assertRaises(ConnectorError) as raised:
                list(GitConnector(self.connection).fetch_commits())

        self.assertIn("owner", str(raised.exception))
        loader.assert_not_called()

    def test_認証失敗の文言にトークンが含まれない(self):
        fake = FakeRequests([FakeResponse(401, {"message": SECRET_TOKEN})])

        with mock.patch.dict("os.environ", {TOKEN_ENV: SECRET_TOKEN}):
            with mock.patch.object(git_module, "_load_requests", return_value=fake):
                with self.assertRaises(ConnectorError) as raised:
                    list(GitConnector(self.connection).fetch_commits())

        message = str(raised.exception)
        self.assertIn("認証に失敗", message)
        self.assertNotIn(SECRET_TOKEN, message)

    def test_応答をExternalCommitへ写す(self):
        payload = [
            {
                "sha": "0123456789abcdef0123456789abcdef01234567",
                "html_url": "https://github.com/example-corp/pmo-agent/commit/0123456",
                "commit": {
                    "message": "fix: 帳票の合計欄を修正\n\n詳細",
                    "author": {"name": "佐藤 健", "date": "2026-07-20T09:00:00Z"},
                },
            }
        ]
        fake = FakeRequests([FakeResponse(200, payload)])

        with mock.patch.dict("os.environ", {TOKEN_ENV: SECRET_TOKEN}):
            with mock.patch.object(git_module, "_load_requests", return_value=fake):
                commits = list(GitConnector(self.connection).fetch_commits())

        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0].summary, "fix: 帳票の合計欄を修正")
        self.assertEqual(commits[0].author, "佐藤 健")
        self.assertEqual(fake.calls[0]["params"]["sha"], "main")
        # トークンはヘッダにしか載らない。
        self.assertIn("Authorization", fake.calls[0]["headers"])


class GitStatsTests(TestCase):
    """日次集計と異常検知。DB を使わない純関数として検証する。"""

    def _commit(self, *, hours_ago: int, additions: int = 10, deletions: int = 5) -> ExternalCommit:
        return ExternalCommit(
            sha=f"sha{hours_ago:03d}",
            message="fix: 仕様変更の反映",
            committed_at=timezone.make_aware(ANCHOR) - timedelta(hours=hours_ago),
            additions=additions,
            deletions=deletions,
            changed_files=2,
        )

    def test_日次に畳んで合計を出す(self):
        activity = summarize_commits(
            [self._commit(hours_ago=1), self._commit(hours_ago=2), self._commit(hours_ago=26)],
            reference_date=date(2026, 7, 20),
        )

        self.assertEqual(len(activity.days), 14)
        self.assertEqual(activity.total_commits, 3)
        self.assertEqual(activity.total_churn, 45)
        self.assertEqual(activity.busiest.day, date(2026, 7, 20))
        self.assertEqual(activity.busiest.commits, 2)
        self.assertEqual(activity.ignored_count, 0)

    def test_期間外のコミットは捨てるが件数は残す(self):
        activity = summarize_commits(
            [self._commit(hours_ago=24 * 60), ExternalCommit(sha="x", committed_at=None)],
            reference_date=date(2026, 7, 20),
        )

        self.assertEqual(activity.total_commits, 0)
        self.assertEqual(activity.ignored_count, 2)
        self.assertFalse(activity.has_anomaly)

    def test_集中した日を異常として理由付きで返す(self):
        commits = [self._commit(hours_ago=hours) for hours in (1, 2, 3, 4)]
        commits.append(self._commit(hours_ago=26))

        activity = summarize_commits(commits, reference_date=date(2026, 7, 20))

        self.assertTrue(activity.has_anomaly)
        self.assertEqual(activity.tone, "a")
        self.assertEqual(activity.anomalies[0].day, date(2026, 7, 20))
        self.assertEqual(activity.anomalies[0].commits, 4)
        self.assertIn("倍", activity.anomalies[0].reason)

    def test_平常時は異常としない(self):
        commits = [self._commit(hours_ago=hours) for hours in (1, 26, 50, 74)]

        activity = summarize_commits(commits, reference_date=date(2026, 7, 20))

        self.assertFalse(activity.has_anomaly)
        self.assertEqual(activity.tone, "n")

    def test_観測日数が少なければ異常判定しない(self):
        days = tuple(
            DailyActivity(day=date(2026, 7, 18) + timedelta(days=offset), commits=offset * 5)
            for offset in range(3)
        )

        self.assertEqual(detect_anomalies(days), ())

    def test_モックコミットから異常日を導ける(self):
        tenant = Tenant.objects.create(code="gt3", name="テナントGT3")
        connection = _connection(tenant, name="Git（集計）")
        connector = GitConnector(connection, reference_time=timezone.make_aware(ANCHOR))

        activity = summarize_commits(
            connector.fetch_commits(), reference_date=date(2026, 7, 20)
        )

        self.assertEqual(activity.total_commits, 10)
        self.assertTrue(activity.has_anomaly)
        self.assertEqual(activity.anomalies[0].day, date(2026, 7, 20))
