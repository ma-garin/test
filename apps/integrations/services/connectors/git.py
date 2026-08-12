"""Git（GitHub）コネクタ。

PMO がコミット履歴を見る目的は、開発の進捗を数えることではない。
**仕様変更の頻度が異常な箇所を見つけること**（traceability #40 の材料）。
同じファイル群が短期間に何度も書き換わっている状態は、要件が固まっていない、
または手戻りが起きている兆候で、課題票より先に現れる。

設計上の判断:

- **課題ではなく履歴として扱う。** `ExternalIssue` へ写すと、状態も期限も無い
  コミットに嘘の既定値が入る。`ExternalCommit` を定義し、`fetch_commits()` を使う。
- **既定はモック。** トークンが無くても 10 件返る。集計（git_stats）まで
  外部依存なしに通せる状態を保つ。
- **`requests` は遅延 import する。** モックしか使わない環境に requests を強制しない。
- **資格情報は持ち出さない。** 例外・ログ・ConnectionStatus には環境変数の
  「名前」までしか載せない。

TODO(親タスク): `Provider` に GIT を追加したら `provider` をその値へ差し替える。
本対応では `apps/integrations/models.py` を変更できないため、文字列で持つ。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from django.utils import timezone

from .base import BaseConnector, ConnectionStatus, ConnectorError

#: GitHub REST API。Enterprise は base_url で差し替えられるようにする。
DEFAULT_API_BASE = "https://api.github.com"
REPO_PATH = "/repos/{owner}/{repo}"
COMMITS_PATH = "/repos/{owner}/{repo}/commits"

HTTP_TIMEOUT: tuple[float, float] = (5.0, 20.0)

#: 1 リクエストの件数と、1 回の取得で扱う上限。
PAGE_SIZE = 50
MAX_COMMITS = 200

#: 変更行数は一覧APIでは返らない。必要なときだけコミット詳細を引くが、
#: 全件引くと N+1 で API 制限に当たるため上限を置く。
MAX_STATS_COMMITS = 30

#: プロバイダ識別子。`Provider` へ追加されるまでは文字列で扱う。
PROVIDER_GIT = "git"


@dataclass(frozen=True)
class ExternalCommit:
    """コミット 1 件。集計（git_stats）が必要とする最小限だけ持つ。"""

    sha: str
    message: str = ""
    author: str = ""
    committed_at: datetime | None = None
    url: str = ""
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def churn(self) -> int:
        """変更行数（追加＋削除）。仕様変更の規模の代理指標として使う。"""

        return self.additions + self.deletions

    @property
    def short_sha(self) -> str:
        return self.sha[:7]

    @property
    def summary(self) -> str:
        """コミットメッセージの 1 行目。画面に出すのはここだけで足りる。"""

        return self.message.splitlines()[0].strip() if self.message else ""


def _load_requests() -> Any:
    """`requests` を遅延 import する。

    テストではこの関数を差し替えることで、実通信なしに LIVE 経路を検証できる。
    """

    try:
        import requests  # noqa: PLC0415  # 実 API モードでのみ必要
    except ModuleNotFoundError as exc:  # pragma: no cover - 環境依存
        raise ConnectorError(
            "実APIモードには requests パッケージが必要です。未導入の場合はモックモードをご利用ください"
        ) from exc

    return requests


class GitConnector(BaseConnector):
    """GitHub からコミット履歴を取り込む。"""

    provider: str = PROVIDER_GIT

    def __init__(self, connection, *, reference_time: datetime | None = None) -> None:
        super().__init__(connection)
        # モックの日時は「今」を基準に決定的に組み立てる。固定日時にすると
        # 時間の経過で「直近14日のコミット頻度」が常に 0 件になり、異常検知の題材にならない。
        self._reference_time = reference_time or timezone.now()

    # ── 疎通確認 ────────────────────────────────────────────

    def check(self) -> ConnectionStatus:
        if not self.connection.is_live:
            return ConnectionStatus(
                ok=True,
                message="モックモードです。APIキー無しでサンプルコミットを10件返します",
                detail={"mode": "mock", "commits": len(MOCK_COMMIT_SEEDS)},
            )

        owner, repo = self._require_repository()
        payload = self._get(REPO_PATH.format(owner=owner, repo=repo), params=None)
        full_name = str(payload.get("full_name") or f"{owner}/{repo}")
        default_branch = str(payload.get("default_branch") or "")

        return ConnectionStatus(
            ok=True,
            # トークンは載せない。載せるのは「どのリポジトリへ繋がったか」だけ。
            message=f"接続できました（リポジトリ: {full_name}）",
            detail={"mode": "live", "repository": full_name, "default_branch": default_branch},
        )

    # ── 取得 ────────────────────────────────────────────────

    def fetch_commits(self) -> Iterable[ExternalCommit]:
        """コミット履歴を取得する。課題ではないので `fetch_issues` は実装しない。"""

        if not self.connection.is_live:
            return self._mock_commits()

        return self._live_commits()

    # ── モック ──────────────────────────────────────────────

    def _mock_commits(self) -> list[ExternalCommit]:
        """仕様変更が集中している状態を再現する。

        乱数を使わない。同じ基準時刻なら常に同じ結果になる。
        直近日に帳票仕様の修正が固まって並ぶよう並べてあり、
        `git_stats.summarize_commits()` が異常日として拾える。
        """

        owner, repo = self._repository_or_default()
        base = f"https://github.com/{owner}/{repo}/commit"
        commits: list[ExternalCommit] = []

        for seed in MOCK_COMMIT_SEEDS:
            committed = self._reference_time - timedelta(hours=seed["hours_ago"])

            commits.append(
                ExternalCommit(
                    sha=seed["sha"],
                    message=seed["message"],
                    author=seed["author"],
                    committed_at=committed,
                    url=f"{base}/{seed['sha']}",
                    additions=seed["additions"],
                    deletions=seed["deletions"],
                    changed_files=seed["changed_files"],
                    raw={"source": "mock", "sha": seed["sha"]},
                )
            )

        return commits

    # ── 実 API ──────────────────────────────────────────────

    def _live_commits(self) -> list[ExternalCommit]:
        owner, repo = self._require_repository()
        branch = str(self.connection.config.get("branch") or "").strip()
        path = COMMITS_PATH.format(owner=owner, repo=repo)

        commits: list[ExternalCommit] = []
        page = 1

        while len(commits) < MAX_COMMITS:
            per_page = min(PAGE_SIZE, MAX_COMMITS - len(commits))
            params: dict[str, Any] = {"per_page": per_page, "page": page}

            if branch:
                params["sha"] = branch

            payload = self._get(path, params=params, expect="list")

            if not isinstance(payload, list) or not payload:
                break

            commits.extend(
                self._to_commit(raw, owner=owner, repo=repo)
                for raw in payload
                if isinstance(raw, dict)
            )
            page += 1

            # 返ってきた件数が要求より少なければ最終ページ。進捗が無ければ必ず抜ける。
            if len(payload) < per_page:
                break

        if self.connection.config.get("with_stats"):
            commits = self._with_stats(commits, owner=owner, repo=repo)

        return commits

    def _with_stats(
        self, commits: list[ExternalCommit], *, owner: str, repo: str
    ) -> list[ExternalCommit]:
        """変更行数を補う。

        一覧APIは行数を返さないため、コミットごとに詳細を引く必要がある。
        既定で行わないのは N+1 になるから。件数上限を超える分は 0 のままにし、
        「取れなかった」ことを黙って平均へ混ぜないようにする。
        """

        enriched: list[ExternalCommit] = []

        for index, commit in enumerate(commits):
            if index >= MAX_STATS_COMMITS:
                enriched.append(commit)
                continue

            detail = self._get(
                f"{COMMITS_PATH.format(owner=owner, repo=repo)}/{commit.sha}", params=None
            )
            stats = detail.get("stats") if isinstance(detail.get("stats"), dict) else {}
            files = detail.get("files")

            # frozen dataclass なので、書き換えずに新しい値で作り直す。
            enriched.append(
                ExternalCommit(
                    sha=commit.sha,
                    message=commit.message,
                    author=commit.author,
                    committed_at=commit.committed_at,
                    url=commit.url,
                    additions=_as_int(stats.get("additions")),
                    deletions=_as_int(stats.get("deletions")),
                    changed_files=len(files) if isinstance(files, list) else 0,
                    raw=commit.raw,
                )
            )

        return enriched

    # ── 設定の取り出し（境界での検証） ──────────────────────

    def _api_base(self) -> str:
        """API のベース。未設定なら GitHub Cloud を使う。"""

        return (self.connection.base_url or DEFAULT_API_BASE).strip().rstrip("/")

    def _require_repository(self) -> tuple[str, str]:
        owner = str(self.connection.config.get("owner") or "").strip()
        repo = str(self.connection.config.get("repo") or "").strip()

        if not owner or not repo:
            raise ConnectorError(
                "リポジトリが未設定です。接続設定の config に owner と repo"
                "（例: owner=example-corp, repo=pmo-agent）を指定してください"
            )

        return owner, repo

    def _repository_or_default(self) -> tuple[str, str]:
        """モック用。未設定でも例外にしない（モックは設定なしで動くのが前提）。"""

        owner = str(self.connection.config.get("owner") or "example-corp").strip()
        repo = str(self.connection.config.get("repo") or "pmo-agent").strip()

        return owner or "example-corp", repo or "pmo-agent"

    # ── HTTP ────────────────────────────────────────────────

    def _get(self, path: str, *, params: dict | None, expect: str = "dict") -> Any:
        """GET して JSON を返す。失敗は必ず日本語の ConnectorError に翻訳する。"""

        http = _load_requests()
        token = self.require_credential()
        url = f"{self._api_base()}{path}"

        try:
            response = http.get(
                url,
                params=params,
                headers={
                    "Accept": "application/vnd.github+json",
                    # ヘッダにしか載せない。ログにも例外にも出さない。
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=HTTP_TIMEOUT,
            )
        except Exception as exc:  # requests の例外階層をここで日本語へ翻訳する
            raise ConnectorError(_describe_transport_error(exc)) from exc

        status_code = int(getattr(response, "status_code", 0) or 0)

        if status_code >= 400:
            raise ConnectorError(_describe_http_error(status_code))

        payload = _safe_json(response)
        expected_type = list if expect == "list" else dict

        if not isinstance(payload, expected_type):
            raise ConnectorError(
                "GitHub の応答が想定した形式ではありませんでした。ベースURLを確認してください"
            )

        return payload

    # ── 正規化 ──────────────────────────────────────────────

    def _to_commit(self, raw: dict, *, owner: str, repo: str) -> ExternalCommit:
        """GitHub の JSON を共通形へ写す。欠損があっても落とさない。"""

        commit = raw.get("commit") if isinstance(raw.get("commit"), dict) else {}
        author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
        stats = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
        sha = str(raw.get("sha") or "")

        return ExternalCommit(
            sha=sha,
            message=str(commit.get("message") or ""),
            author=str(author.get("name") or "").strip(),
            committed_at=_parse_datetime(author.get("date")),
            url=str(raw.get("html_url") or f"https://github.com/{owner}/{repo}/commit/{sha}"),
            additions=_as_int(stats.get("additions")),
            deletions=_as_int(stats.get("deletions")),
            changed_files=0,
            raw=raw,
        )


# ── ヘルパ ──────────────────────────────────────────────────


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_json(response: Any) -> Any:
    try:
        return response.json()
    except Exception:  # noqa: BLE001 - 応答本文の形式は保証されない
        return None


def _describe_transport_error(exc: Exception) -> str:
    name = type(exc).__name__

    if "Timeout" in name:
        return (
            f"GitHub への接続がタイムアウトしました"
            f"（接続{HTTP_TIMEOUT[0]:.0f}秒 / 読み取り{HTTP_TIMEOUT[1]:.0f}秒）。"
            "ネットワークが遮断されている可能性があります"
        )

    if "SSL" in name:
        return "GitHub との TLS 接続に失敗しました。社内プロキシの証明書設定を確認してください"

    if "ConnectionError" in name or "Proxy" in name:
        return "GitHub へ接続できませんでした。ネットワーク／プロキシの疎通を確認してください"

    return "GitHub への通信に失敗しました。ベースURLとネットワーク設定を確認してください"


def _describe_http_error(status_code: int) -> str:
    """HTTP ステータスを、利用者が直せる日本語にする。応答本文は載せない。"""

    if status_code == 401:
        return (
            "GitHub の認証に失敗しました。アクセストークンが正しくないか失効しています。"
            "トークンを再発行し、環境変数を更新してください"
        )

    if status_code == 403:
        return (
            "GitHub へのアクセスが拒否されました。トークンに対象リポジトリの参照権限"
            "（repo スコープ）が無いか、API利用制限に達しています"
        )

    if status_code == 404:
        return (
            "リポジトリが見つかりませんでした。接続設定の owner / repo の綴りと、"
            "トークンが private リポジトリを参照できるかを確認してください"
        )

    if status_code == 409:
        return "リポジトリにコミットがまだありません（空のリポジトリです）"

    if status_code == 422:
        return "指定したブランチが見つかりませんでした。接続設定の branch を確認してください"

    if status_code == 429:
        return "GitHub のAPI利用制限に達しました。時間をおいて再実行してください"

    if status_code >= 500:
        return f"GitHub 側でエラーが発生しています（HTTP {status_code}）。時間をおいて再実行してください"

    return f"GitHub の呼び出しに失敗しました（HTTP {status_code}）"


# ── モックデータ ────────────────────────────────────────────
#
# 「帳票仕様の修正が特定の 1 日に集中する」形にしてある。仕様変更頻度の
# 異常検知（traceability #40）が、モックだけで意味のある結果を出せるようにするため。
# hours_ago は基準時刻からの差。デモと自動テストが同じ結果を見る。

MOCK_COMMIT_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345601",
        "message": "fix: 帳票の合計欄に税区分を追加（CR-014 反映）",
        "author": "佐藤 健",
        "hours_ago": 3,
        "additions": 128,
        "deletions": 42,
        "changed_files": 6,
    },
    {
        "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345602",
        "message": "fix: 帳票の改ページ条件を30行から28行へ変更",
        "author": "佐藤 健",
        "hours_ago": 6,
        "additions": 64,
        "deletions": 51,
        "changed_files": 4,
    },
    {
        "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345603",
        "message": "fix: 帳票レイアウトの余白指定を業務部門の指摘どおりに修正",
        "author": "中村 彩",
        "hours_ago": 9,
        "additions": 33,
        "deletions": 29,
        "changed_files": 3,
    },
    {
        "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345604",
        "message": "refactor: 帳票出力サービスから書式判定を分離",
        "author": "中村 彩",
        "hours_ago": 11,
        "additions": 96,
        "deletions": 88,
        "changed_files": 5,
    },
    {
        "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345605",
        "message": "feat: 検索画面に索引を追加し応答時間を改善（INC-2026-018）",
        "author": "伊藤 翔",
        "hours_ago": 30,
        "additions": 41,
        "deletions": 5,
        "changed_files": 2,
    },
    {
        "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345606",
        "message": "test: 結合テスト用の擬似データ生成を追加",
        "author": "高橋 直子",
        "hours_ago": 54,
        "additions": 210,
        "deletions": 0,
        "changed_files": 7,
    },
    {
        "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345607",
        "message": "docs: 運用手順書のリラン手順を更新",
        "author": "渡辺 亮",
        "hours_ago": 78,
        "additions": 18,
        "deletions": 4,
        "changed_files": 1,
    },
    {
        "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345608",
        "message": "feat: 移行データの文字コード変換を追加",
        "author": "鈴木 一郎",
        "hours_ago": 102,
        "additions": 175,
        "deletions": 12,
        "changed_files": 5,
    },
    {
        "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345609",
        "message": "fix: 権限判定でテナント境界を必ず通すよう修正",
        "author": "山田 太郎",
        "hours_ago": 126,
        "additions": 57,
        "deletions": 23,
        "changed_files": 4,
    },
    {
        "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345610",
        "message": "chore: CI のテスト実行を並列化",
        "author": "小林 誠",
        "hours_ago": 150,
        "additions": 22,
        "deletions": 9,
        "changed_files": 2,
    },
)
