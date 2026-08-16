"""Microsoft Teams Incoming Webhook コネクタ。

MessageCard 形式を使う。Adaptive Card ではなく MessageCard なのは、
Teams の Incoming Webhook がこの形式を素で受け取れるため。
Adaptive Card は `attachments` 包みが要る分、送信側の失敗要因が増える。
"""

from __future__ import annotations

from typing import Any

from apps.integrations.models import Provider
from apps.integrations.services.connectors.webhook import WebhookConnector

#: 重大通知であることを色でも示す。Teams はカード左端の帯に出る。
THEME_COLOR: str = "D64545"


def to_html_text(text: str) -> str:
    """MessageCard の text は HTML として解釈されるので、改行とタグを整える。

    エスケープしないと、外部システム由来のタイトルに含まれる `<` で
    カード全体が表示されなくなる。
    """

    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    return escaped.replace("\n", "<br>")


class TeamsConnector(WebhookConnector):
    provider = Provider.TEAMS
    expected_hosts = ("office.com", "microsoft.com", "azure.com")

    def build_payload(self, *, title: str, body: str, channel: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            # summary が無いと Teams 側で 400 になる。通知一覧に出る文言でもある。
            "summary": title,
            "themeColor": THEME_COLOR,
            "title": title,
            "text": to_html_text(body),
        }

        if channel:
            # Teams の Webhook は投稿先が URL 側で決まる。宛先名は本文に出して、
            # 「どのチャンネル向けの通知か」を人が読めるようにするだけ。
            payload["sections"] = [{"facts": [{"name": "宛先", "value": channel}]}]

        return payload
