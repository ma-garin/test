"""Redmine コネクタ。

PMO の現場では課題が既に Redmine にある。ここへ二重入力させない限り、
このシステムの課題一覧は「誰も更新しない画面」になる。取込経路を作るのが目的。

方針:

- **既定はモック。** API キー無しで同期の端から端まで通せる。デモも試験もこれで回る。
- **モックも実 API も同じ正規化関数を通す。** マッピングの経路を 2 本持つと、
  モックでは通るのに本番で落ちる、という一番たちの悪い壊れ方をする。
- **ページングを必ず追う。** Redmine の `/issues.json` は 1 回 100 件が上限。
  追わないと「同期は成功したのにチケットが足りない」という、気付けない欠落になる。
- **API キーは例外文言にもログにも出さない。** 出すのは環境変数の「名前」まで。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

from .base import (
    BaseConnector,
    ConnectionStatus,
    ConnectorError,
    ExternalIssue,
)

#: HTTP のタイムアウト（秒）。未設定だと同期ジョブが無期限に居座り、
#: 「実行中」のまま誰も気付かないジョブが溜まる。接続と読取で分けて渡す。
HTTP_TIMEOUT: tuple[float, float] = (5.0, 30.0)

#: Redmine REST API の 1 リクエストあたりの上限。これを超える指定は無視される。
MAX_PAGE_SIZE = 100

#: ページングの安全弁。total_count が壊れている連携先で無限ループにしないため。
MAX_PAGES = 100

#: モックが返す URL のベース。接続に base_url が無いときの表示用。
MOCK_BASE_URL = "https://redmine.example.com"


def _http() -> Any:
    """`requests` を遅延インポートする。

    未導入の環境（テストや、モック運用だけの環境）でこのモジュールを
    import できなくなるのを避けるため。テストはこの関数を差し替える。
    """

    try:
        import requests
    except ModuleNotFoundError as exc:  # pragma: no cover - 実行環境依存
        raise ConnectorError(
            "requests がインストールされていないため、実 API モードを利用できません"
        ) from exc

    return requests


def _parse_date(value: Any) -> date | None:
    """Redmine の日付（"YYYY-MM-DD"）を date へ。壊れた値は None にして取込は続行する。"""

    if not isinstance(value, str) or not value.strip():
        return None

    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    """Redmine の更新日時（"2026-07-01T09:00:00Z"）を datetime へ。"""

    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()

    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _name_of(value: Any) -> str:
    """Redmine の関連オブジェクト（{"id": 1, "name": "..."}）から名前だけ取り出す。"""

    if isinstance(value, dict):
        return str(value.get("name") or "").strip()

    return ""


class RedmineConnector(BaseConnector):
    """Redmine のチケットを `ExternalIssue` として取り込む。"""

    provider = "redmine"

    # ── 設定の読み出し ──────────────────────────────────────

    @property
    def _config(self) -> dict:
        config = getattr(self.connection, "config", None)

        return config if isinstance(config, dict) else {}

    def _base_url(self) -> str:
        """末尾のスラッシュを落とした Redmine のベース URL。"""

        return (getattr(self.connection, "base_url", "") or "").rstrip("/")

    def _project_identifier(self) -> str:
        """必須設定。無いまま叩くと全プロジェクトを舐めてしまうので、ここで止める。"""

        value = str(self._config.get("project_identifier") or "").strip()

        if not value:
            raise ConnectorError(
                "接続設定に project_identifier（Redmine のプロジェクト識別子）がありません"
            )

        return value

    def _page_size(self) -> int:
        """1 リクエストの件数。Redmine 側の上限 100 に丸める。"""

        raw = self._config.get("limit", MAX_PAGE_SIZE)

        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = MAX_PAGE_SIZE

        return max(1, min(value, MAX_PAGE_SIZE))

    # ── HTTP ────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        """Redmine の JSON API を 1 回叩く。失敗理由は利用者が読める日本語にする。

        API キーはヘッダにだけ載せる。クエリに載せるとアクセスログや
        リファラに残るため、例外文言だけでなく経路そのものから外す。
        """

        base = self._base_url()

        if not base:
            raise ConnectorError("接続設定にベース URL がありません")

        url = f"{base}{path}"
        headers = {
            "X-Redmine-API-Key": self.require_credential(),
            "Accept": "application/json",
        }

        http = _http()

        try:
            response = http.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
        except ConnectorError:
            raise
        except Exception as exc:  # requests.RequestException 等
            # ネットワーク層の例外は種類で分けても利用者に返す文言は同じなので、まとめて包む。
            # 元の例外は from で残し、開発者はスタックトレースで追える。
            raise ConnectorError(f"Redmine へ接続できませんでした（{url}）") from exc

        status_code = int(getattr(response, "status_code", 0) or 0)

        if status_code in (401, 403):
            raise ConnectorError(
                "Redmine の認証に失敗しました。"
                f"環境変数 {self.connection.credential_env or '未設定'} の API キーと権限を確認してください"
            )

        if status_code == 404:
            raise ConnectorError(
                f"Redmine のリソースが見つかりません（{url}）。プロジェクト識別子を確認してください"
            )

        if status_code >= 400:
            raise ConnectorError(f"Redmine が HTTP {status_code} を返しました（{url}）")

        try:
            payload = response.json()
        except Exception as exc:
            raise ConnectorError(f"Redmine の応答を JSON として解釈できませんでした（{url}）") from exc

        if not isinstance(payload, dict):
            raise ConnectorError(f"Redmine の応答の形式が想定と異なります（{url}）")

        return payload

    # ── 正規化 ──────────────────────────────────────────────

    def _to_issue(self, raw: dict) -> ExternalIssue:
        """Redmine のチケット JSON を `ExternalIssue` へ写す。

        トラッカー（バグ／機能／サポート）とバージョン・カテゴリはラベルへ寄せる。
        内部モデルに列を増やすと連携先ごとの都合が漏れ出すため、ここで吸収する。
        """

        issue_id = str(raw.get("id") or "").strip()
        base = self._base_url() or MOCK_BASE_URL

        labels: list[str] = []

        for value in (
            _name_of(raw.get("tracker")),
            _name_of(raw.get("category")),
            _name_of(raw.get("fixed_version")),
        ):
            if value and value not in labels:
                labels.append(value)

        done_ratio = raw.get("done_ratio")

        if isinstance(done_ratio, int):
            # 進捗率はラベルとして持たせる。数値列を増やさずに一覧で見えるようにするため。
            labels.append(f"進捗{done_ratio}%")

        return ExternalIssue(
            external_id=issue_id,
            key=f"#{issue_id}" if issue_id else "",
            title=str(raw.get("subject") or "").strip(),
            description=str(raw.get("description") or "").strip(),
            status=_name_of(raw.get("status")),
            priority=_name_of(raw.get("priority")),
            assignee=_name_of(raw.get("assigned_to")),
            due_date=_parse_date(raw.get("due_date")),
            url=f"{base}/issues/{issue_id}" if issue_id else "",
            updated_at=_parse_datetime(raw.get("updated_on")),
            labels=tuple(labels),
            raw=raw,
        )

    # ── 契約の実装 ──────────────────────────────────────────

    def check(self) -> ConnectionStatus:
        if not self.connection.is_live:
            return ConnectionStatus(
                ok=True,
                message="モックモードです。API キー無しでサンプルのチケットを取り込めます",
                detail={"mode": "mock", "issues": len(MOCK_ISSUES)},
            )

        payload = self._get("/users/current.json", {})
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        name = " ".join(
            part
            for part in (str(user.get("lastname") or ""), str(user.get("firstname") or ""))
            if part
        ).strip()
        display = name or str(user.get("login") or "").strip() or "（名前不明）"

        return ConnectionStatus(
            ok=True,
            message=f"Redmine に接続できました（{display}）",
            detail={"login": str(user.get("login") or ""), "name": display},
        )

    def fetch_issues(self) -> Iterable[ExternalIssue]:
        if not self.connection.is_live:
            return [self._to_issue(raw) for raw in MOCK_ISSUES]

        return self._fetch_live_issues()

    def _fetch_live_issues(self) -> list[ExternalIssue]:
        """`/issues.json` を total_count に達するまで辿る。

        offset ページングは並び順が安定していないと取りこぼす。Redmine の既定は
        更新日時順で、同期中に更新が入ると順序が動くため、id 昇順を明示している。
        """

        params_base: dict[str, Any] = {
            "project_id": self._project_identifier(),
            "sort": "id",
        }

        status_id = str(self._config.get("status_id") or "").strip()

        if status_id:
            # 未指定なら Redmine の既定（オープンのみ）に従う。閉じた課題も欲しい場合は "*"。
            params_base["status_id"] = status_id

        page_size = self._page_size()
        issues: list[ExternalIssue] = []
        offset = 0

        for _ in range(MAX_PAGES):
            payload = self._get(
                "/issues.json", {**params_base, "limit": page_size, "offset": offset}
            )
            chunk = payload.get("issues")

            if not isinstance(chunk, list):
                raise ConnectorError("Redmine の応答に issues がありません")

            issues.extend(self._to_issue(raw) for raw in chunk if isinstance(raw, dict))

            try:
                total = int(payload.get("total_count", len(issues)))
            except (TypeError, ValueError):
                total = len(issues)

            offset += len(chunk)

            # 空ページが返ったら total_count が過大なので打ち切る。無いと無限ループになる。
            if not chunk or offset >= total:
                break

        return issues


#: モックのチケット。実 API と同じ形（Redmine の JSON）で持ち、`_to_issue` を共用する。
#:
#: 日付は固定値。今日からの相対日にすると、日が変わるたび fingerprint が動いて
#: 全件が「更新」になり、冪等性の検証が意味を失う。期限超過は基準日 2026-07-01 で判定する。
MOCK_ISSUES: tuple[dict, ...] = (
    {
        "id": 1041,
        "subject": "実績工数が二重に集計される",
        "description": "月次で工数を締めた後に修正すると、修正前の値が残り合計が合わない。",
        "tracker": {"id": 1, "name": "バグ"},
        "status": {"id": 2, "name": "進行中"},
        "priority": {"id": 5, "name": "急いで"},
        "assigned_to": {"id": 11, "name": "佐藤 健一"},
        "category": {"id": 3, "name": "工数管理"},
        "fixed_version": {"id": 2, "name": "v1.4"},
        "due_date": "2026-06-05",
        "done_ratio": 60,
        "updated_on": "2026-06-28T02:15:00Z",
    },
    {
        "id": 1042,
        "subject": "週次進捗レポートの自動生成",
        "description": "毎週金曜に手作業で作っている進捗報告を、WBS の実績から生成したい。",
        "tracker": {"id": 2, "name": "機能"},
        "status": {"id": 1, "name": "新規"},
        "priority": {"id": 4, "name": "通常"},
        "assigned_to": {"id": 12, "name": "田中 美咲"},
        "fixed_version": {"id": 3, "name": "v1.5"},
        "due_date": "2026-07-10",
        "done_ratio": 0,
        "updated_on": "2026-06-20T08:40:00Z",
    },
    {
        "id": 1043,
        "subject": "課題管理票の起票ルールについての問い合わせ",
        "description": "協力会社から、起票時に必須となる項目の一覧が欲しいと依頼あり。",
        "tracker": {"id": 3, "name": "サポート"},
        "status": {"id": 4, "name": "フィードバック"},
        "priority": {"id": 3, "name": "低め"},
        "assigned_to": {"id": 13, "name": "鈴木 亮"},
        "due_date": None,
        "done_ratio": 20,
        "updated_on": "2026-06-25T11:05:00Z",
    },
    {
        "id": 1044,
        "subject": "WBS インポートで日本語のタスク名が文字化けする",
        "description": "Shift_JIS の CSV を取り込むと、タスク名が全て「?」になる。",
        "tracker": {"id": 1, "name": "バグ"},
        "status": {"id": 2, "name": "進行中"},
        "priority": {"id": 5, "name": "急いで"},
        "assigned_to": {"id": 14, "name": "高橋 直樹"},
        "category": {"id": 1, "name": "取込"},
        "due_date": "2026-06-20",
        "done_ratio": 80,
        "updated_on": "2026-06-29T06:30:00Z",
    },
    {
        "id": 1045,
        "subject": "EVM（出来高）指標をダッシュボードへ追加",
        "description": "PV / EV / AC と SPI・CPI を案件ごとに表示したい。",
        "tracker": {"id": 2, "name": "機能"},
        "status": {"id": 3, "name": "解決"},
        "priority": {"id": 4, "name": "通常"},
        "assigned_to": {"id": 15, "name": "伊藤 千夏"},
        "fixed_version": {"id": 3, "name": "v1.5"},
        "due_date": "2026-07-31",
        "done_ratio": 100,
        "updated_on": "2026-06-27T13:12:00Z",
    },
    {
        "id": 1046,
        "subject": "協力会社アカウントの権限変更依頼",
        "description": "参画終了に伴い、3 名を閲覧のみへ変更する。",
        "tracker": {"id": 3, "name": "サポート"},
        "status": {"id": 5, "name": "終了"},
        "priority": {"id": 4, "name": "通常"},
        "assigned_to": {"id": 16, "name": "渡辺 悠"},
        "category": {"id": 4, "name": "アカウント"},
        "due_date": "2026-05-30",
        "done_ratio": 100,
        "updated_on": "2026-05-30T09:00:00Z",
    },
    {
        "id": 1047,
        "subject": "アラート通知メールが二重に届く",
        "description": "遅延アラートが同じ内容で 2 通届く。再送処理の重複と思われる。",
        "tracker": {"id": 1, "name": "バグ"},
        "status": {"id": 2, "name": "進行中"},
        "priority": {"id": 4, "name": "通常"},
        "assigned_to": {"id": 17, "name": "山本 彩"},
        "due_date": "2026-07-25",
        "done_ratio": 40,
        "updated_on": "2026-06-30T04:55:00Z",
    },
    {
        "id": 1048,
        "subject": "Redmine との双方向同期の是非を検討",
        "description": "現在は取込のみ。書き戻しは正の所在が曖昧になるため要検討。",
        "tracker": {"id": 2, "name": "機能"},
        "status": {"id": 1, "name": "新規"},
        "priority": {"id": 5, "name": "急いで"},
        "assigned_to": {"id": 18, "name": "中村 大輔"},
        "due_date": "2026-08-14",
        "done_ratio": 10,
        "updated_on": "2026-06-18T07:20:00Z",
    },
    {
        "id": 1049,
        "subject": "旧システムからのデータ移行についての相談",
        "description": "移行対象の範囲が決まらず、依頼元へ差し戻した。",
        "tracker": {"id": 3, "name": "サポート"},
        "status": {"id": 6, "name": "却下"},
        "priority": {"id": 3, "name": "低め"},
        "assigned_to": None,
        "due_date": None,
        "done_ratio": 0,
        "updated_on": "2026-05-12T02:00:00Z",
    },
    {
        "id": 1050,
        "subject": "課題一覧のページングで最終ページが空になる",
        "description": "件数がページサイズの倍数のとき、最後に空ページが表示される。",
        "tracker": {"id": 1, "name": "バグ"},
        "status": {"id": 7, "name": "差戻し"},
        "priority": {"id": 4, "name": "通常"},
        "assigned_to": {"id": 19, "name": "加藤 修"},
        "category": {"id": 2, "name": "画面"},
        "due_date": "2026-06-28",
        "done_ratio": 50,
        "updated_on": "2026-06-26T15:45:00Z",
    },
)
