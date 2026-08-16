"""Incoming Webhook 方式のコネクタ共通処理。

Slack と Teams は「送信先ごとに固有の URL を秘密として持ち、そこへ JSON を
POST する」点が同じで、違うのは本文の形だけ。共通部分をここへ置き、
各コネクタは `build_payload()` だけを実装する。

秘密の扱いについて（ここが一番壊れやすい）:

- Webhook URL は資格情報そのもの。URL を知っている人は誰でも投稿できる。
  したがって `NotificationLog` にも例外文言にも**絶対に載せない**。
- `requests` の例外は文字列表現に URL を含むことがある（`HTTPSConnectionPool`
  や `url:` の行）。そのため例外は握り潰して定型文へ置き換え、`raise ... from None`
  で連鎖も切る。トレースバックに元の例外が残ると、そこから漏れる。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from django.utils import timezone

from apps.integrations.models import NotificationLog
from apps.integrations.services.connectors.base import (
    BaseConnector,
    ConnectionStatus,
    ConnectorError,
    NotificationResult,
)

#: 送信のタイムアウト（秒）。通知でジョブ全体を止めないため、必ず設定する。
#: 未設定の requests は無期限に待つので、これが無いと同期処理が固まる。
WEBHOOK_TIMEOUT_SECONDS: float = 10.0

#: 本文の上限。Slack の section block は 3000 文字、Teams も実質同程度で切られる。
MAX_BODY_CHARS: int = 2800

#: 件名の上限。NotificationLog.title が 300 文字なので、それに合わせる。
MAX_TITLE_CHARS: int = 300


def truncate(text: str, limit: int) -> str:
    """上限を超えたら末尾を省略する。切り捨てたことが読み手に分かるようにする。"""

    if len(text) <= limit:
        return text

    return text[: limit - 1] + "…"


class WebhookConnector(BaseConnector):
    """Incoming Webhook 共通の実装。"""

    #: 期待するホスト（部分一致）。合わなくてもエラーにはせず注意に留める。
    #: Power Automate 等、正規の経路でもホストが変わることがあるため。
    expected_hosts: tuple[str, ...] = ()

    # ── 疎通確認 ────────────────────────────────────────────

    def check(self) -> ConnectionStatus:
        """設定の妥当性だけを見る。**実際には送らない。**

        疎通確認のたびにチャンネルへテスト投稿が飛ぶと、確認するほど迷惑になる。
        Webhook には「送らずに検証する」API が無いので、URL の形式検証で止める。
        """

        if not self.connection.is_live:
            return ConnectionStatus(
                ok=True,
                message="モックモードです。実際の送信は行いません。",
                detail={"mode": self.connection.mode},
            )

        url = self.credential()

        if not url:
            return ConnectionStatus(
                ok=False,
                message=(
                    "Webhook URL が設定されていません"
                    f"（環境変数名: {self.connection.credential_env or '未設定'}）"
                ),
                detail={"credential_env": self.connection.credential_env},
            )

        problem = self._validate_url(url)

        if problem:
            # URL 本体は返さない。何が悪いかだけを返す。
            return ConnectionStatus(ok=False, message=problem, detail={})

        host = urlparse(url).netloc

        if self.expected_hosts and not any(host.endswith(h) for h in self.expected_hosts):
            return ConnectionStatus(
                ok=True,
                message=(
                    f"形式は正しいですが、想定と異なるホストです（{host}）。"
                    "意図した宛先か確認してください。"
                ),
                detail={"host": host},
            )

        return ConnectionStatus(
            ok=True,
            message="Webhook URL の形式は正しいです（実送信はしていません）。",
            detail={"host": host},
        )

    @staticmethod
    def _validate_url(url: str) -> str:
        """形式上の問題を 1 件返す。問題が無ければ空文字。URL 自体は返さない。"""

        parsed = urlparse(url)

        if parsed.scheme != "https":
            return "Webhook URL は https で始まる必要があります"

        if not parsed.netloc:
            return "Webhook URL にホスト名が含まれていません"

        if parsed.path in ("", "/"):
            return "Webhook URL にパスが含まれていません（発行された URL を確認してください）"

        return ""

    # ── 送信 ────────────────────────────────────────────────

    def build_payload(self, *, title: str, body: str, channel: str) -> dict[str, Any]:
        """連携先ごとの本文を組み立てる。サブクラスで実装する。"""

        raise NotImplementedError

    def send(
        self,
        *,
        title: str,
        body: str = "",
        channel: str = "",
        trigger: str = "",
    ) -> NotificationResult:
        """通知を送り、結果を必ず `NotificationLog` へ残す。

        `trigger` は「何に対する通知か」を表す抑止キー。同じキーの送信済み履歴が
        あれば、呼び出し側（notify.py）が二重送信を止める。
        """

        target = channel or str(self.connection.config.get("channel", "") or "")
        safe_title = truncate(title, MAX_TITLE_CHARS)
        safe_body = truncate(body, MAX_BODY_CHARS)

        log_fields = {
            "connection": self.connection,
            "channel": target[:200],
            "title": safe_title,
            "body": safe_body,
            "trigger": trigger[:64],
        }

        if not self.connection.is_live:
            # モックでは外部へ出さない。経路が通ったことだけを履歴に残す。
            # API キー無しで端から端まで通せる状態を保つのが目的。
            NotificationLog.objects.create(
                status=NotificationLog.Status.SENT,
                sent_at=timezone.now(),
                **log_fields,
            )
            return NotificationResult(ok=True, message="モックモードのため実送信していません")

        if not self.credential():
            # 送信を試みてすらいないので FAILED ではなく SKIPPED。
            # 「鍵が無い」と「送って断られた」は対処が違うので、履歴で分ける。
            reason = (
                "Webhook URL が未設定のため送信しませんでした"
                f"（環境変数名: {self.connection.credential_env or '未設定'}）"
            )
            NotificationLog.objects.create(
                status=NotificationLog.Status.SKIPPED, error=reason, **log_fields
            )
            return NotificationResult(ok=False, message=reason)

        payload = self.build_payload(title=safe_title, body=safe_body, channel=target)

        try:
            result = self._post(payload)
        except ConnectorError as exc:
            NotificationLog.objects.create(
                status=NotificationLog.Status.FAILED, error=str(exc), **log_fields
            )
            return NotificationResult(ok=False, message=str(exc))

        NotificationLog.objects.create(
            status=NotificationLog.Status.SENT, sent_at=timezone.now(), **log_fields
        )

        return result

    def _post(self, payload: dict[str, Any]) -> NotificationResult:
        """実 API へ POST する。例外文言に URL を絶対に含めない。"""

        url = self.require_credential()

        try:
            # 遅延 import。モックモードだけを使う環境に requests を強制しない。
            import requests

            response = requests.post(url, json=payload, timeout=WEBHOOK_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - 種類ごとの分岐より漏洩防止を優先する
            # str(exc) には URL が入り得るので使わない。型名だけ残す。
            # `from None` は連鎖した例外（＝URL を含むトレース）を切るため。
            raise ConnectorError(
                f"{self.provider} への送信に失敗しました（{type(exc).__name__}）"
            ) from None

        status_code = getattr(response, "status_code", 0)

        if status_code >= 400:
            raise ConnectorError(f"{self.provider} が HTTP {status_code} を返しました")

        return NotificationResult(ok=True, message=f"{self.provider} へ送信しました")
