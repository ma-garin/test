"""コネクタの解決。

取込側（`sync.py`）や画面が「どの連携先か」を意識しないで済むよう、
`Connection` から実装を引く入り口をここ 1 か所に置く。

設計の要点:

- **遅延 import にする。** 連携先ごとの実装は独立して増えていく。ここで
  まとめて import すると、1 つのモジュールが壊れただけで連携画面全体が
  開かなくなる。実際に必要になった時点で読み込み、失敗しても他は使える。
- **モックへ落とせる。** 実装がまだ無いプロバイダでも、モックモードなら
  内蔵の代替コネクタで同期経路を端から端まで通せる。API キーが無いと
  何も試せない状態にしないための保険。
"""

from __future__ import annotations

import logging
from importlib import import_module

from apps.integrations.services.connectors.base import (
    BaseConnector,
    ConnectionStatus,
    ConnectorError,
    ExternalIssue,
    NotificationResult,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BaseConnector",
    "ConnectionStatus",
    "ConnectorError",
    "ExternalIssue",
    "NotificationResult",
    "get_connector",
]

#: プロバイダ → (モジュール名, クラス名)。ここへ 1 行足すだけで連携先が増える状態を保つ。
REGISTRY: dict[str, tuple[str, str]] = {
    "jira": ("jira", "JiraConnector"),
    "redmine": ("redmine", "RedmineConnector"),
    "slack": ("slack", "SlackConnector"),
    "teams": ("teams", "TeamsConnector"),
    # 文書供給（Confluence）とコミット履歴（Git）。課題取込でも通知でもないため、
    # `Connection.can_pull_issues` / `can_notify` はいずれも False のまま。
    # 取込はそれぞれ `confluence_sync.run_confluence_pull()` と
    # `git_stats.summarize_commits()` が入口になる。
    # TODO(親タスク): `Provider` へ CONFLUENCE / GIT を追加する。models.py は本対応で
    # 変更できないため、当面は provider を素の文字列（"confluence" / "git"）で扱う。
    # 追加後も TextChoices の値は同じ文字列にすること（既存データの読み替えが不要になる）。
    "confluence": ("confluence", "ConfluenceConnector"),
    "git": ("git", "GitConnector"),
}


def _load_connector_class(provider: str) -> type | None:
    """プロバイダ実装のクラスを取り出す。未実装・読み込み失敗なら None。

    例外を握りつぶすのではなく、警告として記録したうえで None を返す。
    画面を落とさないことを優先しつつ、原因はログに残す。
    """

    entry = REGISTRY.get(provider)

    if entry is None:
        return None

    module_name, class_name = entry

    try:
        module = import_module(f"{__name__}.{module_name}")
    except ImportError:
        # まだ実装されていないだけ。想定内なので警告にしない。
        return None
    except Exception:  # noqa: BLE001 - 実装側の不具合で連携画面ごと落とさない
        logger.warning("コネクタ %s の読み込みに失敗しました", provider, exc_info=True)

        return None

    connector_class = getattr(module, class_name, None)

    if not isinstance(connector_class, type):
        logger.warning("コネクタ %s に %s が見つかりません", provider, class_name)

        return None

    return connector_class


def get_connector(connection) -> BaseConnector:
    """接続設定に対応するコネクタを返す。

    実装が見つからない場合、モードがモックなら内蔵の代替へ落とす。
    実 API モードで実装が無いときは、黙って何もしないのではなく明確に失敗させる
    （「同期したのに 0 件」を成功として見せない）。
    """

    from apps.integrations.models import Connection

    provider = getattr(connection, "provider", "")
    connector_class = _load_connector_class(provider)

    if connector_class is not None:
        return connector_class(connection)

    if provider not in REGISTRY:
        raise ConnectorError(f"未知の連携先です（{provider or '未設定'}）")

    if getattr(connection, "mode", Connection.Mode.MOCK) == Connection.Mode.MOCK:
        from apps.integrations.services.connectors.fallback import FallbackMockConnector

        return FallbackMockConnector(connection)

    raise ConnectorError(
        f"{connection.get_provider_display()} の実 API 連携はまだ利用できません。"
        "モードをモックにすれば取込経路の確認だけは行えます"
    )
