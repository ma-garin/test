"""Jira コネクタ。

PMO の課題は既に Jira にある。ここへ二重入力させた時点で更新は止まるので、
このコネクタは「既にある Jira の課題を、内部の共通形（ExternalIssue）へ写す」ことだけをする。
外へは何も書かない（片方向）。

設計上の判断:

- **既定はモック。** API キーが無くても `fetch_issues()` が 10 件返す。
  同期経路（sync → SyncedRecord → 画面）を、外部依存なしに端から端まで通せる状態を保つ。
  `LocalHashEmbedder` と同じ理由。
- **`requests` は遅延 import する。** モックしか使わない環境に requests を強制しないため。
  実 API を使うときだけ必要になる。
- **例外は必ず日本語にする。** 「401」だけを画面に出しても利用者は直せない。
  認証・権限・プロジェクト不明を区別し、次にどこを見ればよいかまで書く。
- **資格情報は文字列として一切持ち出さない。** 例外・ログ・ConnectionStatus には
  環境変数の「名前」までしか載せない。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

from django.utils import timezone

from apps.integrations.models import Provider

from .base import BaseConnector, ConnectionStatus, ConnectorError, ExternalIssue

#: Jira Cloud REST API v3 のパス。
SEARCH_PATH = "/rest/api/3/search"
MYSELF_PATH = "/rest/api/3/myself"

#: HTTP タイムアウト（接続, 読み取り）秒。
#: 無限待ちにすると同期ジョブが running のまま固まり、次の実行も詰まる。
HTTP_TIMEOUT: tuple[float, float] = (5.0, 20.0)

#: 1 リクエストで取る件数と、1 回の同期で取り込む上限。
#: 上限を置かないと、巨大プロジェクトで同期が終わらなくなる。
PAGE_SIZE = 50
MAX_ISSUES = 200

#: 正規化に必要な最小限のフィールドだけ要求する。全件取ると転送量が跳ねる。
SEARCH_FIELDS = "summary,description,status,priority,assignee,duedate,labels,updated"


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


class JiraConnector(BaseConnector):
    """Jira Cloud から課題を取り込む。"""

    provider: str = Provider.JIRA

    def __init__(self, connection, *, reference_date: date | None = None) -> None:
        super().__init__(connection)
        # モックの期限は「今日」を基準に組み立てる。固定日付にすると時間の経過で
        # 全件が期限超過になり、期限超過の混在というサンプルの意図が失われるため。
        # 基準日を引数で固定できるので、テストでは決定的に扱える。
        self._reference_date = reference_date or timezone.localdate()

    # ── 疎通確認 ────────────────────────────────────────────

    def check(self) -> ConnectionStatus:
        """設定を保存する前に、利用者が自分で試せるようにする。"""

        if not self.connection.is_live:
            return ConnectionStatus(
                ok=True,
                message="モックモードです。APIキー無しでサンプル課題を10件返します",
                detail={"mode": "mock", "issues": len(MOCK_ISSUE_SEEDS)},
            )

        base_url = self._require_base_url()
        email = self._require_email()
        token = self.require_credential()

        payload = self._get(MYSELF_PATH, auth=(email, token), params=None)
        display_name = str(payload.get("displayName") or payload.get("emailAddress") or "").strip()

        return ConnectionStatus(
            ok=True,
            # トークンは載せない。載せるのは「誰として繋がったか」だけ。
            message=f"接続できました（接続先ユーザー: {display_name or '不明'}）",
            detail={"mode": "live", "base_url": base_url, "display_name": display_name},
        )

    # ── 取込 ────────────────────────────────────────────────

    def fetch_issues(self) -> Iterable[ExternalIssue]:
        if not self.connection.is_live:
            return self._mock_issues()

        return self._live_issues()

    # ── モック ──────────────────────────────────────────────

    def _mock_issues(self) -> list[ExternalIssue]:
        """PMO の現場で実際に見かける課題を、決定的に組み立てる。

        乱数を使わない。同じ基準日なら常に同じ結果になる。
        """

        base_url = (self.connection.base_url or "https://example.atlassian.net").rstrip("/")
        # 更新日時も基準日から決定的に導く。同じ入力で同じ結果になることを崩さない。
        anchor = datetime.combine(self._reference_date, datetime.min.time())
        anchor = timezone.make_aware(anchor) if timezone.is_naive(anchor) else anchor

        issues: list[ExternalIssue] = []

        for seed in MOCK_ISSUE_SEEDS:
            due = self._reference_date + timedelta(days=seed["due_offset"])
            updated = anchor - timedelta(hours=seed["updated_hours_ago"])

            issues.append(
                ExternalIssue(
                    external_id=seed["external_id"],
                    key=seed["key"],
                    title=seed["title"],
                    description=seed["description"],
                    status=seed["status"],
                    priority=seed["priority"],
                    assignee=seed["assignee"],
                    due_date=due,
                    url=f"{base_url}/browse/{seed['key']}",
                    updated_at=updated,
                    labels=tuple(seed["labels"]),
                    raw={
                        "source": "mock",
                        "key": seed["key"],
                        "due_offset": seed["due_offset"],
                    },
                )
            )

        return issues

    # ── 実 API ──────────────────────────────────────────────

    def _live_issues(self) -> list[ExternalIssue]:
        self._require_base_url()
        email = self._require_email()
        token = self.require_credential()
        jql = self._build_jql()

        issues: list[ExternalIssue] = []
        start_at = 0

        while len(issues) < MAX_ISSUES:
            payload = self._get(
                SEARCH_PATH,
                auth=(email, token),
                params={
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": min(PAGE_SIZE, MAX_ISSUES - len(issues)),
                    "fields": SEARCH_FIELDS,
                },
            )

            page = payload.get("issues") or []

            if not isinstance(page, list) or not page:
                break

            issues.extend(self._to_issue(raw) for raw in page if isinstance(raw, dict))

            total = payload.get("total")
            start_at += len(page)

            # total を信じ切らず、進捗が無くなったら必ず抜ける（無限ループ防止）。
            if not isinstance(total, int) or start_at >= total:
                break

        return issues

    def _build_jql(self) -> str:
        """プロジェクトキーを必ず条件に入れる。

        利用者が書いた JQL をそのまま使うと、別プロジェクトの課題まで
        この接続に紐付いて取り込まれてしまう。案件の分離を JQL 任せにしない。
        """

        project_key = self._require_project_key()
        extra = str(self.connection.config.get("jql") or "").strip().rstrip(";")

        clause = f'project = "{project_key}"'

        if extra:
            clause = f"{clause} AND ({extra})"

        return f"{clause} ORDER BY updated DESC"

    # ── 設定の取り出し（境界での検証） ──────────────────────

    def _require_base_url(self) -> str:
        base_url = (self.connection.base_url or "").strip().rstrip("/")

        if not base_url:
            raise ConnectorError(
                "ベースURLが未設定です。接続設定に Jira のURL（例: https://example.atlassian.net）を入力してください"
            )

        return base_url

    def _require_project_key(self) -> str:
        key = str(self.connection.config.get("project_key") or "").strip()

        if not key:
            raise ConnectorError(
                "プロジェクトキーが未設定です。接続設定の config に project_key（例: PMO）を指定してください"
            )

        return key

    def _require_email(self) -> str:
        email = str(self.connection.config.get("email") or "").strip()

        if not email:
            raise ConnectorError(
                "接続ユーザーのメールアドレスが未設定です。"
                "接続設定の config に email（APIトークンを発行した Atlassian アカウント）を指定してください"
            )

        return email

    # ── HTTP ────────────────────────────────────────────────

    def _get(self, path: str, *, auth: tuple[str, str], params: dict | None) -> dict:
        """GET して JSON を返す。失敗は必ず日本語の ConnectorError に翻訳する。"""

        http = _load_requests()
        url = f"{self._require_base_url()}{path}"

        try:
            response = http.get(
                url,
                params=params,
                auth=auth,
                headers={"Accept": "application/json"},
                timeout=HTTP_TIMEOUT,
            )
        except Exception as exc:  # requests の例外階層をここで日本語へ翻訳する
            raise ConnectorError(_describe_transport_error(exc)) from exc

        status_code = int(getattr(response, "status_code", 0) or 0)

        if status_code >= 400:
            raise ConnectorError(_describe_http_error(status_code, _safe_json(response)))

        payload = _safe_json(response)

        if not isinstance(payload, dict):
            raise ConnectorError(
                "Jira の応答が想定した形式ではありませんでした。ベースURLがJira Cloudのものか確認してください"
            )

        return payload

    # ── 正規化 ──────────────────────────────────────────────

    def _to_issue(self, raw: dict) -> ExternalIssue:
        """Jira のフィールドを共通形へ写す。

        Jira 側の欠損（担当未割当・期限なし・優先度なし）は普通に起きるので、
        欠けていても落とさず空文字 / None に倒す。
        """

        fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
        key = str(raw.get("key") or "")
        base_url = (self.connection.base_url or "").strip().rstrip("/")

        return ExternalIssue(
            external_id=str(raw.get("id") or key),
            key=key,
            title=str(fields.get("summary") or "").strip(),
            description=_adf_to_text(fields.get("description")),
            status=_nested_name(fields.get("status")),
            priority=_nested_name(fields.get("priority")),
            assignee=_display_name(fields.get("assignee")),
            due_date=_parse_date(fields.get("duedate")),
            url=f"{base_url}/browse/{key}" if base_url and key else str(raw.get("self") or ""),
            updated_at=_parse_datetime(fields.get("updated")),
            labels=_labels(fields.get("labels")),
            raw=raw,
        )


# ── 変換ヘルパ ──────────────────────────────────────────────


def _nested_name(value: Any) -> str:
    """`{"name": "対応中"}` 形式から名前だけ取る。"""

    if isinstance(value, dict):
        return str(value.get("name") or "").strip()

    return ""


def _display_name(value: Any) -> str:
    """担当者。未割当（None）は空文字にする。"""

    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("name") or "").strip()

    return ""


def _labels(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()

    return tuple(str(item).strip() for item in value if str(item).strip())


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    """Jira の更新日時（例: `2026-07-20T10:12:33.000+0900`）を読む。

    形式が変わっても取込全体を落とさないよう、読めなければ None に倒す。
    """

    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip().replace("Z", "+00:00")

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _adf_to_text(value: Any) -> str:
    """Atlassian Document Format（v3 の本文）を素のテキストへ落とす。

    v3 の description は JSON ツリーで返る。画面と RAG で使うのは文章だけなので、
    段落単位で改行に潰す。書式は捨てる（復元しないと決めた方が、後で揺れない）。
    """

    if isinstance(value, str):
        return value.strip()

    if not isinstance(value, dict):
        return ""

    lines: list[str] = []

    def walk(node: Any, buffer: list[str]) -> None:
        if not isinstance(node, dict):
            return

        if node.get("type") == "text":
            buffer.append(str(node.get("text") or ""))
            return

        children = node.get("content")
        block: list[str] = []

        if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
            for child in children:
                walk(child, block)

        text = "".join(block)

        # 段落・見出し・リスト項目は 1 行として扱う。それ以外は親へ積み上げる。
        if node.get("type") in {"paragraph", "heading", "listItem"}:
            if text.strip():
                lines.append(text.strip())
        else:
            buffer.append(text)

    walk(value, [])

    return "\n".join(lines).strip()


# ── エラーの日本語化 ────────────────────────────────────────


def _safe_json(response: Any) -> Any:
    """本文が JSON でないこと（プロキシの HTML など）は普通に起きる。

    読めなかったことと「空の JSON が返った」ことを区別したいので、失敗は None を返す。
    """

    try:
        return response.json()
    except Exception:  # noqa: BLE001 - 応答本文の形式は保証されない
        return None


def _error_messages(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    messages = payload.get("errorMessages")
    parts: list[str] = []

    if isinstance(messages, list):
        parts.extend(str(item) for item in messages if str(item).strip())

    errors = payload.get("errors")

    if isinstance(errors, dict):
        parts.extend(f"{k}: {v}" for k, v in errors.items())

    return " / ".join(parts)


def _describe_transport_error(exc: Exception) -> str:
    """通信層の失敗を、次に何を見ればよいかまで書いた日本語にする。"""

    name = type(exc).__name__

    if "Timeout" in name:
        return (
            f"Jira への接続がタイムアウトしました（接続{HTTP_TIMEOUT[0]:.0f}秒 / 読み取り{HTTP_TIMEOUT[1]:.0f}秒）。"
            "Jira 側の応答が遅いか、ネットワークが遮断されている可能性があります"
        )

    if "SSL" in name:
        return "Jira との TLS 接続に失敗しました。社内プロキシの証明書設定を確認してください"

    if "ConnectionError" in name or "Proxy" in name:
        return "Jira へ接続できませんでした。ベースURLの綴りと、ネットワーク／プロキシの疎通を確認してください"

    return "Jira への通信に失敗しました。ベースURLとネットワーク設定を確認してください"


def _describe_http_error(status_code: int, payload: Any) -> str:
    """HTTP ステータスを、利用者が直せる日本語にする。

    ステータスコードだけを出すと「401 と出ています」で問い合わせが止まる。
    認証・権限・対象不明は原因も直し方も別物なので、必ず区別する。
    """

    detail = _error_messages(payload)
    suffix = f"（Jiraからの応答: {detail}）" if detail else ""

    if status_code == 401:
        return (
            "Jira の認証に失敗しました。メールアドレスと API トークンの組み合わせが正しくありません。"
            "トークンが失効している場合は Atlassian で再発行し、環境変数を更新してください"
        )

    if status_code == 403:
        return (
            "Jira へのアクセスが拒否されました。認証は通っていますが、"
            "このアカウントに対象プロジェクトの閲覧権限がありません。Jira の権限設定を確認してください"
            + suffix
        )

    if status_code == 404:
        return (
            "Jira の対象が見つかりませんでした。ベースURL、またはプロジェクトキーが正しいか確認してください"
            + suffix
        )

    if status_code == 400:
        # Jira は「存在しないプロジェクト」も「JQL の構文誤り」も 400 で返す。
        # 直す場所が違うので、応答本文を見て切り分ける。
        lowered = detail.lower()

        if "project" in lowered:
            return (
                "指定したプロジェクトが Jira に存在しないか、参照できません。"
                "接続設定の project_key を確認してください" + suffix
            )

        return "検索条件（JQL）を Jira が解釈できませんでした。接続設定の jql を確認してください" + suffix

    if status_code == 429:
        return "Jira のAPI利用制限に達しました。時間をおいて再実行してください"

    if status_code >= 500:
        return f"Jira 側でエラーが発生しています（HTTP {status_code}）。時間をおいて再実行してください"

    return f"Jira の呼び出しに失敗しました（HTTP {status_code}）{suffix}"


# ── モックデータ ────────────────────────────────────────────
#
# PMO の現場で実際に上がる課題を並べる。状態・優先度・担当・期限・ラベルに
# ばらつきを持たせ、期限超過（due_offset が負）を意図的に混ぜている。
# デモと自動テストの両方がこの 1 か所を見るので、値は決め打ちで持つ。

MOCK_ISSUE_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "external_id": "10001",
        "key": "PMO-101",
        "title": "帳票レイアウトの仕様が確定せず、設計工程が着手できない",
        "description": "第3回要件確認会で保留となった帳票（請求書・納品書）のレイアウトが未確定。"
        "業務部門の回答待ちで、詳細設計の着手判定が下ろせない。",
        "status": "対応中",
        "priority": "High",
        "assignee": "佐藤 健",
        "due_offset": -6,
        "updated_hours_ago": 30,
        "labels": ("要件定義", "遅延"),
    },
    {
        "external_id": "10002",
        "key": "PMO-102",
        "title": "検証環境の払い出しがインフラ部門で滞留している",
        "description": "結合テスト用の検証環境について申請から3週間が経過。"
        "払い出しが完了しないとテスト開始日が後ろ倒しになる。",
        "status": "対応待ち",
        "priority": "Highest",
        "assignee": "山田 太郎",
        "due_offset": -2,
        "updated_hours_ago": 8,
        "labels": ("環境", "他部門依存"),
    },
    {
        "external_id": "10003",
        "key": "PMO-103",
        "title": "データ移行におけるベンダー間の責任分界点が未合意",
        "description": "旧システム側の抽出をA社、変換・投入をB社が担う想定だが、"
        "文字コード変換の不具合時の一次対応がどちらの範囲か合意できていない。",
        "status": "対応中",
        "priority": "High",
        "assignee": "鈴木 一郎",
        "due_offset": 3,
        "updated_hours_ago": 20,
        "labels": ("調達", "契約", "移行"),
    },
    {
        "external_id": "10004",
        "key": "PMO-104",
        "title": "テストデータの個人情報マスキング方針が未策定",
        "description": "本番データを検証環境へ複製する運用を想定しているが、"
        "マスキング対象項目と手順が未定のため、情報セキュリティ部門の承認が取れない。",
        "status": "未着手",
        "priority": "Medium",
        "assignee": "高橋 直子",
        "due_offset": 7,
        "updated_hours_ago": 52,
        "labels": ("テスト", "セキュリティ"),
    },
    {
        "external_id": "10005",
        "key": "PMO-105",
        "title": "性能要件（同時接続100・応答3秒以内）を満たしていない",
        "description": "性能試験の第1回計測で、検索画面の応答が平均8.4秒。"
        "SQLのチューニングだけで到達できるか、方式見直しが要るかの判断が必要。",
        "status": "対応中",
        "priority": "Highest",
        "assignee": "伊藤 翔",
        "due_offset": -1,
        "updated_hours_ago": 5,
        "labels": ("性能", "リスク"),
    },
    {
        "external_id": "10006",
        "key": "PMO-106",
        "title": "単体テスト成果物のレビュー指摘が未クローズ（32件）",
        "description": "レビューで挙がった指摘のうち32件が未対応のまま結合テストへ進もうとしている。"
        "品質ゲートの通過可否を判断する必要がある。",
        "status": "レビュー中",
        "priority": "Medium",
        "assignee": "中村 彩",
        "due_offset": 2,
        "updated_hours_ago": 14,
        "labels": ("品質", "レビュー"),
    },
    {
        "external_id": "10007",
        "key": "PMO-107",
        "title": "変更要求 CR-014 の見積り回答が期日を過ぎている",
        "description": "承認された変更要求について、ベンダーからの工数見積り回答が未着。"
        "回答が無いままではスケジュールへの反映ができない。",
        "status": "対応待ち",
        "priority": "Medium",
        "assignee": "小林 誠",
        "due_offset": 5,
        "updated_hours_ago": 44,
        "labels": ("変更管理",),
    },
    {
        "external_id": "10008",
        "key": "PMO-108",
        "title": "定例会議の議事録が2週間分未展開",
        "description": "決定事項が関係者へ共有されておらず、同じ論点が再燃している。担当者が未割当のまま。",
        "status": "未着手",
        "priority": "Low",
        "assignee": "",
        "due_offset": -9,
        "updated_hours_ago": 96,
        "labels": ("プロジェクト運営",),
    },
    {
        "external_id": "10009",
        "key": "PMO-109",
        "title": "移行リハーサルの実施日程が未確定",
        "description": "業務部門の繁忙期を避ける必要があり、候補日が絞れていない。"
        "リハーサル日が決まらないと切替判定会の日程も置けない。",
        "status": "未着手",
        "priority": "High",
        "assignee": "加藤 美咲",
        "due_offset": 14,
        "updated_hours_ago": 70,
        "labels": ("移行", "計画"),
    },
    {
        "external_id": "10010",
        "key": "PMO-110",
        "title": "障害対応手順書のレビューが完了",
        "description": "運用部門との合同レビューを実施し、指摘は全て反映済み。次回改訂は運用開始1か月後。",
        "status": "完了",
        "priority": "Low",
        "assignee": "渡辺 亮",
        "due_offset": -20,
        "updated_hours_ago": 120,
        "labels": ("運用", "ドキュメント"),
    },
)
