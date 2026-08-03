"""未確定事項の参照が実在することの検査。

`docs/open_questions.md` は決まった項目を消していく運用のため、
以前は「3番」「7番」と**見出しの位置**で参照していた参照元が、
項目が1つ決まるたびに全部ずれていた。実際に
`poc_evaluation.py` は存在しない「7番」を指し、その文言は
PoC 合否画面に表示されていた。

そこで参照を `OQ-001` 形式の ID へ変え、次を検査する。

1. `open_questions.md` が ID を定義しており、重複していないこと
2. 参照側に書かれた ID が**すべて実在する**こと
3. 位置による参照（「〜番」）が復活していないこと

人の注意力ではなく検査で止める。テンプレートの静的検査
（`test_template_hygiene.py`）と同じ考え方である。
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase

BASE_DIR = Path(settings.BASE_DIR)
OPEN_QUESTIONS = BASE_DIR / "docs" / "open_questions.md"

#: 検査対象。実装（コメント含む）とドキュメントの両方を見る。
SCAN_ROOTS = (BASE_DIR / "apps", BASE_DIR / "docs")
SCAN_SUFFIXES = (".py", ".md")

#: このファイル自身は、禁止したい書き方を説明のために含むので除外する。
SELF = Path(__file__).resolve()

#: 定義側（表の行と見出しの両方で使う）。
DEFINITION = re.compile(r"^\|\s*(OQ-\d{3})\s*\||^##\s*(OQ-\d{3})\b", re.MULTILINE)

#: 参照側。
REFERENCE = re.compile(r"OQ-\d{3}")

#: 位置による参照。「open_questions.md の 8 番」のような書き方を禁じる。
POSITIONAL = re.compile(r"open_questions\.md[^\n]{0,16}?\d+\s*番")


def _scan_files() -> list[Path]:
    files: list[Path] = []

    for root in SCAN_ROOTS:
        for suffix in SCAN_SUFFIXES:
            files.extend(
                path for path in root.rglob(f"*{suffix}") if path.resolve() != SELF
            )

    return sorted(files)


def _defined_ids() -> list[str]:
    source = OPEN_QUESTIONS.read_text(encoding="utf-8")

    return [match.group(1) or match.group(2) for match in DEFINITION.finditer(source)]


class OpenQuestionIdTests(TestCase):
    def setUp(self) -> None:
        self.defined = _defined_ids()

    def test_IDを読み取れる(self) -> None:
        """書式が変わって 0 件のまま「合格」になるのを防ぐ。"""

        self.assertGreaterEqual(len(self.defined), 5, "open_questions.md の ID を読み取れていない")

    def test_IDが重複していない(self) -> None:
        duplicated = sorted({oq for oq in self.defined if self.defined.count(oq) > 1})

        self.assertEqual(duplicated, [], f"ID が重複している: {duplicated}")

    def test_参照した_IDが実在する(self) -> None:
        known = set(self.defined)
        dangling: list[str] = []
        referenced = 0

        for path in _scan_files():
            source = path.read_text(encoding="utf-8")

            for match in REFERENCE.finditer(source):
                referenced += 1

                if match.group(0) not in known:
                    line = source[: match.start()].count("\n") + 1
                    dangling.append(f"{path.relative_to(BASE_DIR)}:{line} {match.group(0)}")

        self.assertEqual(dangling, [], f"実在しない ID を参照している: {dangling}")
        self.assertGreater(referenced, 0, "参照が 1 件も見つからない（検査が空振りしている）")

    def test_位置で参照していない(self) -> None:
        """「3番」で指すと、項目が1つ決まるたびに参照先がずれる。"""

        offenders: list[str] = []

        for path in _scan_files():
            source = path.read_text(encoding="utf-8")

            for match in POSITIONAL.finditer(source):
                line = source[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(BASE_DIR)}:{line}")

        self.assertEqual(
            offenders,
            [],
            "未確定事項は OQ-001 形式の ID で参照してください（位置指定は禁止）: "
            + ", ".join(offenders),
        )
