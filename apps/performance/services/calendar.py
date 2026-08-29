"""月の扱いを1か所へ集める。

計数は月単位でしか持たないので、入力に日が混ざったら必ず月初へ丸める。
丸めをビューごとに書くと「2026-04-01 と 2026-04-15 が別レコードになる」
という壊れ方をして、合計が静かに二重になる。
"""

from __future__ import annotations

import re
from datetime import date

#: "2026-04" / "2026/4" / "2026-04-01" / "202604" を受ける。
MONTH_PATTERN = re.compile(r"^(\d{4})[-/年]?(\d{1,2})(?:[-/月]?(\d{1,2})日?)?$")


def month_start(value: date) -> date:
    return value.replace(day=1)


def next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)

    return date(value.year, value.month + 1, 1)


def months_between(start: date, end: date) -> list[date]:
    """`start` から `end` までの月初日。両端を含む。"""

    if start is None or end is None:
        return []

    months: list[date] = []
    cursor = month_start(start)
    last = month_start(end)

    while cursor <= last:
        months.append(cursor)
        cursor = next_month(cursor)

    return months


def parse_month(text: str) -> date | None:
    """CSV・フォームの月表記を月初日へ。読めなければ None（呼び出し側でエラーにする）。"""

    if not text:
        return None

    matched = MONTH_PATTERN.match(str(text).strip())

    if matched is None:
        return None

    year, month = int(matched.group(1)), int(matched.group(2))

    if not 1 <= month <= 12:
        return None

    return date(year, month, 1)


def format_month(value: date) -> str:
    return f"{value:%Y-%m}" if value else ""


def fiscal_label(value: date) -> str:
    """画面に出す短い月表記。年度をまたぐ表で年が分かるようにする。"""

    return f"{value:%Y/%m}" if value else ""
