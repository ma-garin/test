"""LDF-06: Slack の許可チャンネル読み取り。

会話には途中経過・仮説・誤記・顧客固有の情報が含まれる。会話だけで案件状態を
確定してはならない。ここでは「候補としての事実」を拾うところまでを行う。

不変条件（`docs/改善に.md` 外部連携の実装ポリシー）:
- 許可された案件チャンネルだけを対象にする。DM・非許可チャンネルは取り込まない。
- 全文を無期限に複製しない。要約と短い抜粋、パーマリンク、取得時点だけを持つ。
- 会話由来の関連は候補である。人が確認するまで予測の確定根拠に使わない。
- **読み取り専用。** ここから Slack へ投稿・更新・削除しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from apps.forecast.models.signals import SignalClassification, SignalSource
from apps.forecast.services.ingest import IngestError, receive_event
from apps.forecast.services.linking import apply_proposals, propose_links
from apps.graph.models.graph import Feature
from apps.integrations.models import Connection, Provider

#: Slack 抜粋の上限。会話の全文保存を既定にしないための歯止め。
MAX_SLACK_EXCERPT = 300


class ChannelNotAllowed(PermissionError):
    """許可されていないチャンネル。取り込まずに拒否する。"""


@dataclass(frozen=True)
class SlackMessage:
    """取り込む対象の 1 投稿。Slack API の生ペイロードはここまでで正規化する。"""

    channel: str
    ts: str
    text: str
    author: str = ""
    permalink: str = ""
    thread_ts: str = ""
    occurred_at: datetime | None = None

    @property
    def is_direct_message(self) -> bool:
        """DM チャンネルは既定で対象外。Slack の DM は `D` で始まる。"""

        return self.channel.startswith("D")


@dataclass(frozen=True)
class SlackIngestResult:
    """取り込み結果。拒否した理由も返す（0 件の説明ができるようにする）。"""

    created: int = 0
    duplicates: int = 0
    rejected_channel: int = 0
    rejected_dm: int = 0
    candidate_links: int = 0
    failed: int = 0

    def summary_line(self) -> str:
        return (
            f"取込 {self.created}件 / 重複 {self.duplicates}件 / "
            f"非許可チャンネル {self.rejected_channel}件 / DM {self.rejected_dm}件 / "
            f"関連候補 {self.candidate_links}件"
        )


def allowed_channels(connection: Connection) -> frozenset[str]:
    """接続設定で許可されたチャンネル。未設定なら 1 つも許可しない。

    「未設定なら全部許可」にしない。設定漏れがそのまま情報の持ち出しになるため。
    """

    configured = connection.config.get("channels") or []
    return frozenset(str(channel) for channel in configured)


def ingest_messages(connection: Connection, messages) -> SlackIngestResult:
    """許可チャンネルの投稿を Signal と関連候補にする。"""

    if connection.provider != Provider.SLACK or connection.project_id is None:
        return SlackIngestResult()

    allowed = allowed_channels(connection)
    rules = connection.config.get("channel_feature_rules") or {}
    features = list(Feature.objects.filter(project=connection.project))

    created = duplicates = rejected_channel = rejected_dm = links = failed = 0

    for message in messages:
        if message.is_direct_message:
            rejected_dm += 1
            continue
        if message.channel not in allowed:
            rejected_channel += 1
            continue

        try:
            result = receive_event(
                connection.project,
                source=SignalSource.SLACK,
                event_type="message",
                occurred_at=message.occurred_at,
                payload={"channel": message.channel, "ts": message.ts, "text": message.text},
                external_event_id=f"{message.channel}:{message.ts}",
                summary=_summarize(message.text),
                excerpt=message.text[:MAX_SLACK_EXCERPT],
                permalink=message.permalink,
                classification=SignalClassification.CONVERSATION,
                channel_reference=message.channel,
                author_reference=message.author[:120],
            )
        except (IngestError, TypeError, ValueError):
            failed += 1
            continue

        if result.is_duplicate:
            duplicates += 1
            continue

        created += 1
        proposals = propose_links(result.signal, features=features, rules=rules)
        links += len(apply_proposals(result.signal, proposals))

    return SlackIngestResult(
        created=created,
        duplicates=duplicates,
        rejected_channel=rejected_channel,
        rejected_dm=rejected_dm,
        candidate_links=links,
        failed=failed,
    )


def _summarize(text: str) -> str:
    """要約は 1 行に畳む。原文は permalink で参照する前提を崩さない。"""

    single_line = " ".join(text.split())
    return single_line[:120] or "（本文なし）"
