"""一覧のページング。

サービス層が先頭 N 件で打ち切っていると、集計値（総件数）と実際に見えている行数が
食い違う。「50件」と出ているのに 50 行しか無い状態は、データが無いのか
切られているのか利用者に判別できない。総件数は QuerySet 全体から取り、
表示だけをページ単位で切る。
"""

from __future__ import annotations

from django.core.paginator import EmptyPage, Page, PageNotAnInteger, Paginator
from django.db.models import QuerySet
from django.http import HttpRequest

#: 1 ページあたりの既定件数。業務一覧は 1 画面で俯瞰したいので多めに取る。
PAGE_SIZE = 50

#: 利用者が一覧の密度を選ぶときの候補。任意の巨大値を受け付けない。
PAGE_SIZE_OPTIONS = (20, PAGE_SIZE, 100)

#: ページャに並べる前後のページ数。
WINDOW = 2


def paginate(queryset: QuerySet, request: HttpRequest, per_page: int = PAGE_SIZE) -> Page:
    """`?page=` を解釈して 1 ページ分を返す。

    不正な値でも 404 にしない。URL を手で編集した程度で画面が落ちると、
    一覧から詳細へ戻る導線が壊れるため、範囲外は端のページへ寄せる。
    """

    # カード一覧など個別に密度を決めた画面は、指定値を優先する。
    # 標準一覧だけは ?per_page= で 20 / 50 / 100 件に切り替えられる。
    if per_page == PAGE_SIZE:
        try:
            requested_size = int(request.GET.get("per_page", PAGE_SIZE))
        except (TypeError, ValueError):
            requested_size = PAGE_SIZE
        per_page = requested_size if requested_size in PAGE_SIZE_OPTIONS else PAGE_SIZE

    paginator = Paginator(queryset, per_page)

    try:
        return paginator.page(request.GET.get("page", 1))
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages)


def page_window(page: Page, window: int = WINDOW) -> list[int]:
    """ページャに出す番号。全件並べると桁が増えたときに折り返す。"""

    current = page.number
    last = page.paginator.num_pages

    return [n for n in range(current - window, current + window + 1) if 1 <= n <= last]


def query_without_page(request: HttpRequest) -> str:
    """現在の絞り込み条件を保ったページ送りリンクを作るための文字列。

    絞り込んだ状態で 2 ページ目へ行くと条件が消える、という壊れ方を防ぐ。
    """

    params = request.GET.copy()
    params.pop("page", None)
    encoded = params.urlencode()

    return f"{encoded}&" if encoded else ""
