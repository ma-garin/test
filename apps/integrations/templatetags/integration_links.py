"""内部レコードから外部原文（Jira / Redmine / Slack）へのリンクを描くタグ。

根拠トレースは「AI が何を見たか」だけでは足りない。最後は人が一次情報を開いて
確かめられる必要がある。取り込んだ課題・タスク・不具合には `SyncedRecord` が
付いているので、対応があるものだけリンクを出す。

方針:

- **対応が無ければ何も出さない。** 「—」も出さない（呼び出し側で `as` を使って
  分岐できるようにするため、空文字を返す）。
- **`target="_blank"` には必ず `rel="noopener noreferrer"` を付ける。**
  付けないと遷移先から `window.opener` 経由でこちらのタブを操作できてしまう。
- **URL のスキームを検査する。** 外部から取り込んだ値をそのまま `href` に置くと、
  `javascript:` が入り込んだときにクリックで任意のスクリプトが走る。
"""

from __future__ import annotations

from typing import Any

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

from apps.integrations import selectors

register = template.Library()

#: リンクとして描いてよいスキーム。取り込んだ URL を無検査で信頼しない。
SAFE_SCHEMES = ("http://", "https://")


def _lookup(obj: Any, links: dict):
    """先に引いておいた辞書から取り出す。キーは `(種別, 内部ID)`。"""

    entity_type = selectors.entity_type_for(obj)
    object_id = getattr(obj, "pk", None)

    if entity_type is None or object_id is None:
        return None

    return links.get((entity_type, object_id))


def _is_safe_url(url: str) -> bool:
    """`href` に置いてよい URL か。"""

    return bool(url) and url.lower().startswith(SAFE_SCHEMES)


@register.simple_tag
def external_link(obj: Any, links: dict | None = None, css_class: str = "btn-ghost sm") -> SafeString:
    """内部レコードに対応する外部原文へのリンク。対応が無ければ空文字。

    一覧では `links`（`selectors.synced_records_for()` の結果）を渡すこと。
    渡さないと 1 行ごとに 1 クエリ増える。50 行の一覧で 50 本になり、
    ページングを入れていても体感が落ちる。詳細画面のように 1 件しか
    描かない場所では省略してよい。
    """

    record = _lookup(obj, links) if links is not None else selectors.synced_record_for(obj)

    if record is None or not _is_safe_url(record.external_url):
        return mark_safe("")

    return format_html(
        '<a class="{}" href="{}" target="_blank" rel="noopener noreferrer">{} {} →</a>',
        css_class,
        record.external_url,
        record.connection.get_provider_display(),
        record.external_key or record.external_id,
    )


@register.simple_tag
def external_sources(run: Any, limit: int = 10) -> list:
    """Agentic 実行が参照しうる外部原文の一覧。

    トレース画面から一次情報へ辿れるようにするためのもの。実行そのものと
    レコードの結び付きは持っていないので、**同じテナント・同じ案件の接続から
    取り込んだもの**を出所候補として並べる（推測であることは画面側に書く）。
    """

    return selectors.external_records_for_run(run, limit=limit)
