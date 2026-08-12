"""接続設定の保存と疎通確認。

資格情報の「値」はここでも扱わない。保存するのは環境変数の名前だけで、
値の解決は `BaseConnector.credential()` に閉じている。
"""

from __future__ import annotations

from apps.integrations.models import Connection
from apps.integrations.services.connectors import get_connector
from apps.integrations.services.connectors.base import ConnectionStatus, ConnectorError


def save_connection(form, tenant) -> Connection:
    """バリデーション済みフォームから接続を確定する（新規・更新共通）。

    テナントはフォームから受け取らない。POST で差し替えられると、
    他テナントへ接続を作れてしまうため、必ず現在のテナントを使う。
    """

    connection: Connection = form.save(commit=False)
    connection.tenant = tenant
    connection.save()

    return connection


def check_connection(connection: Connection) -> ConnectionStatus:
    """疎通確認。失敗も例外にせず、画面へ出せる結果として返す。"""

    try:
        return get_connector(connection).check()
    except ConnectorError as error:
        return ConnectionStatus(ok=False, message=str(error))
    except Exception as error:  # noqa: BLE001 - 想定外でも画面を落とさない
        # 例外本文には URL やヘッダが混ざりうるので種別だけを見せる。
        return ConnectionStatus(
            ok=False, message=f"疎通確認に失敗しました（{error.__class__.__name__}）"
        )
