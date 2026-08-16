"""Confluence コネクタ。

RAG の弱点は「対象が手動アップロードの文書だけ」だったこと。設計書も議事録も
Confluence 側で更新され続けるため、取り込む経路が無いと索引は必ず陳腐化する。
このコネクタは「Confluence にあるページを、内部で扱う形（ExternalPage）へ写す」
ことだけをする。外へは書かない（片方向）。

設計上の判断:

- **課題ではなく文書として扱う。** `ExternalIssue`（誰が・いつまでに）へ写すと、
  期限も担当も無いページに嘘の既定値が入る。ページ専用の `ExternalPage` を定義し、
  `BaseConnector.fetch_issues` は実装しない（`fetch_pages()` を使う）。
- **既定はモック。** API キーが無くても 8 件返る。取込（confluence_sync）から
  Document 登録までを外部依存なしに端から端まで通せる状態を保つ。
- **`requests` は遅延 import する。** モックしか使わない環境に requests を強制しない。
- **例外は必ず日本語にする。** 「401」だけでは利用者が直せない。
- **資格情報は持ち出さない。** 例外・ログ・ConnectionStatus には環境変数の
  「名前」までしか載せない。

TODO(親タスク): `Provider` に CONFLUENCE を追加したら `provider` をその値へ差し替える。
本対応では `apps/integrations/models.py` を変更できないため、文字列で持つ。
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from django.utils import timezone

from .base import BaseConnector, ConnectionStatus, ConnectorError

#: Confluence Cloud REST API のパス。Cloud は `/wiki` 配下に置かれる。
CONTENT_PATH = "/wiki/rest/api/content"
SEARCH_PATH = "/wiki/rest/api/content/search"
SPACE_PATH = "/wiki/rest/api/space"

#: HTTP タイムアウト（接続, 読み取り）秒。無限待ちにすると同期ジョブが running のまま固まる。
HTTP_TIMEOUT: tuple[float, float] = (5.0, 20.0)

#: 1 リクエストの件数と、1 回の同期で取り込む上限。
#: 上限が無いと、数千ページある wiki で同期が終わらなくなる。
PAGE_SIZE = 25
MAX_PAGES = 200

#: 本文・版・ラベルまで一度に取る。ページごとに追加リクエストを出すと N+1 になる。
EXPAND = "body.storage,version,space,metadata.labels"

#: プロバイダ識別子。`Provider` へ追加されるまでは文字列で扱う。
PROVIDER_CONFLUENCE = "confluence"


@dataclass(frozen=True)
class ExternalPage:
    """Confluence のページを、内部で扱う形へ正規化したもの。

    `ExternalIssue` と分けている理由は、ページには「状態・優先度・期限・担当」が
    無いため。無い概念に既定値を詰めると、取り込んだ後で本物と区別できなくなる。
    """

    page_id: str
    title: str
    space_key: str = ""
    body_text: str = ""
    version: int = 0
    url: str = ""
    updated_at: datetime | None = None
    labels: tuple[str, ...] = ()
    raw: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """内容のハッシュ。変化が無ければ再登録しないための判定に使う。

        `version` を信じない。ラベル付け替えや移動だけでも版は上がるため、
        本文が同じなら再インデックスしたくない。
        """

        payload = json.dumps(
            {
                "title": self.title,
                "body": self.body_text,
                "space_key": self.space_key,
                "labels": sorted(self.labels),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def content_sha256(self) -> str:
        """本文だけのハッシュ。`Document.sha256`（原本の同一性）へ入れる。"""

        return hashlib.sha256(self.body_text.encode("utf-8")).hexdigest()


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


class ConfluenceConnector(BaseConnector):
    """Confluence Cloud からページを取り込む。"""

    provider: str = PROVIDER_CONFLUENCE

    def __init__(self, connection, *, reference_time: datetime | None = None) -> None:
        super().__init__(connection)
        # モックの更新日時は「今」を基準に決定的に組み立てる。固定日時にすると
        # 時間の経過で全件が「何年も前の文書」になり、鮮度のサンプルとして意味を失う。
        self._reference_time = reference_time or timezone.now()

    # ── 疎通確認 ────────────────────────────────────────────

    def check(self) -> ConnectionStatus:
        if not self.connection.is_live:
            return ConnectionStatus(
                ok=True,
                message="モックモードです。APIキー無しでサンプルページを8件返します",
                detail={"mode": "mock", "pages": len(MOCK_PAGE_SEEDS)},
            )

        base_url = self._require_base_url()
        space_key = self._require_space_key()
        email = self._require_email()
        token = self.require_credential()

        payload = self._get(f"{SPACE_PATH}/{space_key}", auth=(email, token), params=None)
        name = str(payload.get("name") or space_key).strip()

        return ConnectionStatus(
            ok=True,
            # トークンは載せない。載せるのは「どのスペースへ繋がったか」だけ。
            message=f"接続できました（スペース: {name}）",
            detail={"mode": "live", "base_url": base_url, "space_key": space_key},
        )

    # ── 取込 ────────────────────────────────────────────────

    def fetch_pages(self) -> Iterable[ExternalPage]:
        """ページを取得する。課題ではないので `fetch_issues` は実装しない。"""

        if not self.connection.is_live:
            return self._mock_pages()

        return self._live_pages()

    # ── モック ──────────────────────────────────────────────

    def _mock_pages(self) -> list[ExternalPage]:
        """PMO の現場にありそうなページを、決定的に組み立てる。

        乱数を使わない。同じ基準時刻なら常に同じ結果になる。
        """

        base_url = (self.connection.base_url or "https://example.atlassian.net").rstrip("/")
        space_key = str(self.connection.config.get("space_key") or "PMO").strip() or "PMO"
        pages: list[ExternalPage] = []

        for seed in MOCK_PAGE_SEEDS:
            updated = self._reference_time - timedelta(hours=seed["updated_hours_ago"])

            pages.append(
                ExternalPage(
                    page_id=seed["page_id"],
                    title=seed["title"],
                    space_key=space_key,
                    body_text=seed["body"],
                    version=seed["version"],
                    url=f"{base_url}/wiki/spaces/{space_key}/pages/{seed['page_id']}",
                    updated_at=updated,
                    labels=tuple(seed["labels"]),
                    raw={"source": "mock", "page_id": seed["page_id"]},
                )
            )

        return pages

    # ── 実 API ──────────────────────────────────────────────

    def _live_pages(self) -> list[ExternalPage]:
        self._require_base_url()
        space_key = self._require_space_key()
        email = self._require_email()
        token = self.require_credential()
        cql = str(self.connection.config.get("cql") or "").strip()

        pages: list[ExternalPage] = []
        start = 0

        while len(pages) < MAX_PAGES:
            limit = min(PAGE_SIZE, MAX_PAGES - len(pages))

            if cql:
                # CQL を使う場合もスペースを必ず条件へ入れる。利用者の CQL 任せにすると
                # 別スペースの文書がこの接続に紐付いて取り込まれる。
                path = SEARCH_PATH
                params = {
                    "cql": f'space = "{space_key}" AND ({cql})',
                    "expand": EXPAND,
                    "limit": limit,
                    "start": start,
                }
            else:
                path = CONTENT_PATH
                params = {
                    "spaceKey": space_key,
                    "type": "page",
                    "status": "current",
                    "expand": EXPAND,
                    "limit": limit,
                    "start": start,
                }

            payload = self._get(path, auth=(email, token), params=params)
            results = payload.get("results")

            if not isinstance(results, list) or not results:
                break

            pages.extend(self._to_page(raw) for raw in results if isinstance(raw, dict))
            start += len(results)

            # 返ってきた件数が要求より少なければ最終ページ。進捗が無ければ必ず抜ける。
            if len(results) < limit:
                break

        return pages

    # ── 設定の取り出し（境界での検証） ──────────────────────

    def _require_base_url(self) -> str:
        base_url = (self.connection.base_url or "").strip().rstrip("/")

        if not base_url:
            raise ConnectorError(
                "ベースURLが未設定です。接続設定に Confluence のURL"
                "（例: https://example.atlassian.net）を入力してください"
            )

        return base_url

    def _require_space_key(self) -> str:
        key = str(self.connection.config.get("space_key") or "").strip()

        if not key:
            raise ConnectorError(
                "スペースキーが未設定です。接続設定の config に space_key（例: PMO）を指定してください"
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
            raise ConnectorError(_describe_http_error(status_code))

        payload = _safe_json(response)

        if not isinstance(payload, dict):
            raise ConnectorError(
                "Confluence の応答が想定した形式ではありませんでした。"
                "ベースURLが Confluence Cloud のものか確認してください"
            )

        return payload

    # ── 正規化 ──────────────────────────────────────────────

    def _to_page(self, raw: dict) -> ExternalPage:
        """Confluence の JSON を共通形へ写す。欠損があっても落とさない。"""

        base_url = (self.connection.base_url or "").strip().rstrip("/")
        page_id = str(raw.get("id") or "")
        version = raw.get("version") if isinstance(raw.get("version"), dict) else {}
        space = raw.get("space") if isinstance(raw.get("space"), dict) else {}
        links = raw.get("_links") if isinstance(raw.get("_links"), dict) else {}
        webui = str(links.get("webui") or "")

        return ExternalPage(
            page_id=page_id,
            title=str(raw.get("title") or "").strip(),
            space_key=str(space.get("key") or "").strip(),
            body_text=_storage_to_text(_storage_value(raw)),
            version=_as_int(version.get("number")),
            url=f"{base_url}{webui}" if base_url and webui else "",
            updated_at=_parse_datetime(version.get("when")),
            labels=_labels(raw),
            raw=raw,
        )


# ── 変換ヘルパ ──────────────────────────────────────────────

#: storage 形式（XHTML）から本文を取り出すための最小限の置換。
_BLOCK_END = re.compile(r"(?i)</(p|div|li|tr|h[1-6]|td|th)\s*>")
_LINE_BREAK = re.compile(r"(?i)<br\s*/?>")
_DROP_BLOCKS = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_TAGS = re.compile(r"(?s)<[^>]+>")
_BLANK_LINES = re.compile(r"\n{3,}")


def _storage_value(raw: dict) -> str:
    body = raw.get("body") if isinstance(raw.get("body"), dict) else {}
    storage = body.get("storage") if isinstance(body.get("storage"), dict) else {}

    return str(storage.get("value") or "")


def _storage_to_text(value: str) -> str:
    """Confluence storage 形式（XHTML）を素のテキストへ落とす。

    RAG が使うのは文章だけなので、書式は捨てる。ブロック要素の終わりだけ改行へ
    畳むことで、見出しと本文が 1 行に潰れて意味が読めなくなるのを防ぐ。
    """

    if not value:
        return ""

    text = _DROP_BLOCKS.sub("", value)
    text = _LINE_BREAK.sub("\n", text)
    text = _BLOCK_END.sub("\n", text)
    text = _TAGS.sub("", text)
    text = html.unescape(text)
    text = "\n".join(line.strip() for line in text.splitlines())

    return _BLANK_LINES.sub("\n\n", text).strip()


def _labels(raw: dict) -> tuple[str, ...]:
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}
    results = labels.get("results")

    if not isinstance(results, list):
        return ()

    names = [
        str(item.get("name") or "").strip()
        for item in results
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]

    return tuple(names)


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_datetime(value: Any) -> datetime | None:
    """更新日時（例: `2026-07-20T10:12:33.000+09:00`）を読む。

    形式が変わっても取込全体を落とさないよう、読めなければ None に倒す。
    """

    if not isinstance(value, str) or not value.strip():
        return None

    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_json(response: Any) -> Any:
    """本文が JSON でないこと（プロキシの HTML など）は普通に起きる。"""

    try:
        return response.json()
    except Exception:  # noqa: BLE001 - 応答本文の形式は保証されない
        return None


# ── エラーの日本語化 ────────────────────────────────────────


def _describe_transport_error(exc: Exception) -> str:
    name = type(exc).__name__

    if "Timeout" in name:
        return (
            f"Confluence への接続がタイムアウトしました"
            f"（接続{HTTP_TIMEOUT[0]:.0f}秒 / 読み取り{HTTP_TIMEOUT[1]:.0f}秒）。"
            "Confluence 側の応答が遅いか、ネットワークが遮断されている可能性があります"
        )

    if "SSL" in name:
        return "Confluence との TLS 接続に失敗しました。社内プロキシの証明書設定を確認してください"

    if "ConnectionError" in name or "Proxy" in name:
        return (
            "Confluence へ接続できませんでした。ベースURLの綴りと、"
            "ネットワーク／プロキシの疎通を確認してください"
        )

    return "Confluence への通信に失敗しました。ベースURLとネットワーク設定を確認してください"


def _describe_http_error(status_code: int) -> str:
    """HTTP ステータスを、利用者が直せる日本語にする。

    応答本文は文言へ載せない。Confluence はエラー本文へリクエスト内容を
    そのまま含めることがあり、資格情報が混ざる余地を残さないため。
    """

    if status_code == 401:
        return (
            "Confluence の認証に失敗しました。メールアドレスと API トークンの組み合わせが"
            "正しくありません。トークンが失効している場合は Atlassian で再発行し、"
            "環境変数を更新してください"
        )

    if status_code == 403:
        return (
            "Confluence へのアクセスが拒否されました。認証は通っていますが、"
            "このアカウントに対象スペースの閲覧権限がありません。スペースの権限設定を確認してください"
        )

    if status_code == 404:
        return (
            "Confluence の対象が見つかりませんでした。ベースURL、"
            "または接続設定の space_key が正しいか確認してください"
        )

    if status_code == 400:
        return (
            "検索条件（CQL）を Confluence が解釈できませんでした。接続設定の cql を確認してください"
        )

    if status_code == 429:
        return "Confluence のAPI利用制限に達しました。時間をおいて再実行してください"

    if status_code >= 500:
        return f"Confluence 側でエラーが発生しています（HTTP {status_code}）。時間をおいて再実行してください"

    return f"Confluence の呼び出しに失敗しました（HTTP {status_code}）"


# ── モックデータ ────────────────────────────────────────────
#
# PMO の wiki に実際に置かれる種類のページを並べる。設計・議事録・運用手順・
# 障害報告・標準プロセスを混ぜ、更新の新しさにもばらつきを持たせている。
# デモと自動テストの両方がこの 1 か所を見るので、値は決め打ちで持つ。

MOCK_PAGE_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "page_id": "500101",
        "title": "基本設計書 第3章 帳票設計",
        "body": "請求書・納品書のレイアウトと出力条件を定義する。\n"
        "出力形式は PDF（A4縦）。明細行が30行を超える場合は改ページし、"
        "2ページ目以降にも合計欄を再掲する。\n"
        "第3回要件確認会での保留事項（合計欄の税区分表示）は未反映。",
        "version": 7,
        "updated_hours_ago": 6,
        "labels": ("設計書", "帳票"),
    },
    {
        "page_id": "500102",
        "title": "第14回 定例進捗会議 議事録",
        "body": "決定事項: 結合テスト開始日を2週間後ろ倒しし、検証環境の払い出し完了を前提条件とする。\n"
        "保留事項: 性能試験の再計測日程。インフラ部門の回答待ち。\n"
        "宿題: 変更要求 CR-014 の見積り回答をベンダーへ督促（担当: 小林）。",
        "version": 2,
        "updated_hours_ago": 20,
        "labels": ("議事録", "定例会"),
    },
    {
        "page_id": "500103",
        "title": "運用手順書 日次バッチ監視",
        "body": "日次バッチは 02:00 に起動する。監視画面で完了を確認し、"
        "03:30 までに終了しない場合はエスカレーションする。\n"
        "異常終了時はリラン可否をジョブ定義表で確認してから再実行すること。"
        "データ二重投入を避けるため、リラン前に必ず投入済み件数を確認する。",
        "version": 4,
        "updated_hours_ago": 52,
        "labels": ("運用", "手順書"),
    },
    {
        "page_id": "500104",
        "title": "障害報告書 INC-2026-018 検索応答遅延",
        "body": "事象: 検索画面の応答が平均8.4秒となり、性能要件（3秒以内）を満たさなかった。\n"
        "原因: 明細テーブルへの索引が欠落し、全件走査が発生していた。\n"
        "暫定対応: 索引を追加し、平均2.1秒まで改善。\n"
        "恒久対応: 索引設計のレビュー観点を設計標準へ追加する（期限は次回リリースまで）。",
        "version": 3,
        "updated_hours_ago": 30,
        "labels": ("障害報告", "性能"),
    },
    {
        "page_id": "500105",
        "title": "標準プロセス 変更管理手順",
        "body": "変更要求は CR 番号を採番し、影響調査・見積り・承認の3段階で扱う。\n"
        "見積り回答の標準期日は受付から5営業日。期日を超えた場合は PMO が督促し、"
        "週次で未回答一覧を提示する。\n"
        "承認された変更は、WBS とスケジュールへ反映するまでを1つの完了とする。",
        "version": 11,
        "updated_hours_ago": 200,
        "labels": ("標準プロセス", "変更管理"),
    },
    {
        "page_id": "500106",
        "title": "テスト計画書 結合テスト",
        "body": "対象は業務シナリオ42本。合否基準は重大度A・Bの不具合ゼロとする。\n"
        "テストデータは本番相当データをマスキングして用いるが、"
        "マスキング方針が未承認のため、承認前は擬似データで代替する。",
        "version": 5,
        "updated_hours_ago": 74,
        "labels": ("テスト", "計画"),
    },
    {
        "page_id": "500107",
        "title": "移行計画書 リハーサル要領",
        "body": "移行リハーサルは本番同等の手順・時間帯で実施し、切戻し判断の所要時間も計測する。\n"
        "判定会は各リハーサルの翌営業日に開催し、未解決事項が残る場合は次回条件を明記する。",
        "version": 6,
        "updated_hours_ago": 96,
        "labels": ("移行", "計画"),
    },
    {
        "page_id": "500108",
        "title": "セキュリティ要件 個人情報の取扱い",
        "body": "検証環境へ本番データを複製する場合、氏名・住所・電話番号・口座情報をマスキングする。\n"
        "マスキング処理の実施記録は監査証跡として1年間保管する。"
        "情報セキュリティ部門の承認が無い状態での複製は禁止する。",
        "version": 8,
        "updated_hours_ago": 130,
        "labels": ("セキュリティ", "標準プロセス"),
    },
)
