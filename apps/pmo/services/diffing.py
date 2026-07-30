"""AI生成本文と確定本文の差分。

赤字率（数値）だけでは「どこを直したか」が分からない。承認者が見たいのは
割合ではなく差分の中身なので、行単位の差分を画面へ渡せる形にする。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

#: 画面へ出す最大行数。長大な報告書で画面が固まるのを防ぐ。
MAX_DIFF_LINES = 300

SAME = "same"
ADDED = "add"
REMOVED = "del"


@dataclass(frozen=True)
class DiffLine:
    """差分 1 行。`kind` は same/add/del。"""

    kind: str
    text: str

    @property
    def marker(self) -> str:
        return {ADDED: "＋", REMOVED: "−"}.get(self.kind, "　")

    @property
    def tone(self) -> str:
        """既存のバッジ配色に合わせる。g=追記 / r=削除 / n=変更なし。"""

        return {ADDED: "g", REMOVED: "r"}.get(self.kind, "n")


@dataclass(frozen=True)
class DiffResult:
    lines: tuple[DiffLine, ...]
    added: int
    removed: int
    truncated: bool

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


def line_diff(ai_body: str, final_body: str) -> DiffResult:
    """行単位の差分を返す。確定本文が空なら「まだ未編集」として差分は出さない。"""

    if not ai_body or not final_body:
        return DiffResult(lines=(), added=0, removed=0, truncated=False)

    before = ai_body.splitlines()
    after = final_body.splitlines()
    matcher = difflib.SequenceMatcher(None, before, after)
    lines: list[DiffLine] = []
    added = 0
    removed = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            lines.extend(DiffLine(SAME, text) for text in before[i1:i2])
            continue

        if tag in ("replace", "delete"):
            removed += i2 - i1
            lines.extend(DiffLine(REMOVED, text) for text in before[i1:i2])

        if tag in ("replace", "insert"):
            added += j2 - j1
            lines.extend(DiffLine(ADDED, text) for text in after[j1:j2])

    truncated = len(lines) > MAX_DIFF_LINES

    return DiffResult(
        lines=tuple(lines[:MAX_DIFF_LINES]),
        added=added,
        removed=removed,
        truncated=truncated,
    )
