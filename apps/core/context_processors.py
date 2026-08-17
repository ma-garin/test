"""テンプレート共通のナビゲーション情報。"""

from __future__ import annotations

from django.http import HttpRequest
from django.utils import timezone

from apps.core.navigation import ACCOUNT_SECTION, navigation_for

#: 絞り込み条件ではないクエリパラメータ。送信の有無を数えるときに除く。
#: ページ送りは利用者が条件を指定した操作ではない。
NON_FILTER_PARAMS: frozenset[str] = frozenset({"page", "next"})


def navigation(request: HttpRequest) -> dict:
    """サイドバーの描画と active 判定に必要な情報を渡す。

    `current_url_name` は `app_name:url_name` 形式。`NavItem.url_name` と
    そのまま比較できるので、テンプレート側に判定ロジックを書かずに済む。
    """

    match = getattr(request, "resolver_match", None)
    current = ""

    if match is not None:
        current = f"{match.app_name}:{match.url_name}" if match.app_name else (match.url_name or "")

    tenant = getattr(request, "tenant", None)
    project = getattr(request, "project", None)
    if project is not None:
        scope_label = f"{tenant.name if tenant else 'テナント未選択'} ／ {project.code} {project.name}"
    elif tenant is not None:
        scope_label = f"{tenant.name} ／ 全案件"
    else:
        scope_label = "対象テナントを選択してください"

    sections = navigation_for(getattr(request, "user", None), current)

    return {
        "nav_sections": sections,
        # 現在地を含むカテゴリ。アカウントメニューから開く画面のようにメニュー外の
        # 画面では None になる。子メニューを空欄のまま出さないための判定に使う。
        "current_section": next((section for section in sections if section.is_current), None),
        # 上が None のとき、子メニューの位置に出す行き先。機能カテゴリと同じ
        # `NavSection` なので、サイドバーは同一のテンプレートで描ける。
        "account_section": ACCOUNT_SECTION,
        "current_tenant": tenant,
        # 選択中の案件。全画面のヘッダーに出し、いま何を見ているかを常に示す。
        "current_project": project,
        "current_url_name": current,
        # データそのものの更新日時ではなく、画面の集計・表示を確認した時点。
        # これを区別して出すことで、画面をいつ見直したかを利用者が判断できる。
        "page_rendered_at": timezone.localtime(),
        "page_scope_label": scope_label,
        "filter_submitted_empty": _submitted_without_condition(request),
    }


def _submitted_without_condition(request: HttpRequest) -> bool:
    """絞り込みフォームを、条件を1つも指定せずに送信したか。

    `request.GET.get(key, "")` は「キーが無い」と「キーが空」を区別しない。
    区別しないまま同じ画面を返すと、押しても何も起きない画面になり、
    利用者には結果が 0 件なのか機能が壊れているのかが分からない（VP-62）。

    判定はキーの有無で行う。値が 1 つでも入っていれば絞り込みは効いているため
    False を返し、通知は出さない。
    """

    params = getattr(request, "GET", None)
    if not params:
        return False

    keys = [key for key in params if key not in NON_FILTER_PARAMS]

    return bool(keys) and all(not params.get(key, "").strip() for key in keys)
