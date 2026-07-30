"""Slack Incoming Webhook コネクタ。

Block Kit へ整形する理由: プレーンテキストだと件名と本文が同じ重さで流れ、
チャンネルの中で埋もれる。header + section に分けると、一覧で件名だけが読める。
"""

from __future__ import annotations

from typing import Any

from apps.integrations.models import Provider
from apps.integrations.services.connectors.webhook import WebhookConnector, truncate

#: Slack の header block はプレーンテキストで 150 文字まで。超えると 400 になる。
MAX_HEADER_CHARS: int = 150


def escape_mrkdwn(text: str) -> str:
    """mrkdwn の制御文字を無害化する。

    課題タイトルに `<` や `&` が入ると、Slack 側がリンク記法として解釈して
    本文が壊れる。送信内容は外部システム由来なので、必ず通してから送る。
    """

    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SlackConnector(WebhookConnector):
    provider = Provider.SLACK
    expected_hosts = ("slack.com",)

    def build_payload(self, *, title: str, body: str, channel: str) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": truncate(title, MAX_HEADER_CHARS),
                    "emoji": True,
                },
            }
        ]

        if body:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": escape_mrkdwn(body)},
                }
            )

        payload: dict[str, Any] = {
            # text は通知バナー・検索用のフォールバック。blocks だけだと
            # モバイルの通知に「メッセージが届きました」としか出ない。
            "text": truncate(title, MAX_HEADER_CHARS),
            "blocks": blocks,
        }

        if channel:
            # Incoming Webhook は既定の投稿先が固定されている。channel の指定は
            # ワークスペース設定によっては無視されるが、送れる場合のために渡す。
            payload["channel"] = channel

        return payload
