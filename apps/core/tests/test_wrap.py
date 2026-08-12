"""和文の改行位置の指定が消えていないことを固定する。

`word-break: auto-phrase` は Chrome / Edge にしか無いため、Safari / Firefox では
CSS の `keep-all` と JavaScript が挿入する `<wbr>` の組で改行位置を決めている。
JavaScript の実行結果は Django のテストでは確かめられないので、
**その仕組みを成り立たせている指定が残っていること**を回帰の対象にする。
"""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parents[3]
CSS_PATH = BASE_DIR / "static" / "css" / "app.css"
BASE_TEMPLATE_PATH = BASE_DIR / "templates" / "layouts" / "base.html"


class JapaneseLineBreakTests(SimpleTestCase):
    """CSS と base.html の双方に、改行制御の要が残っているか。"""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        cls.template = BASE_TEMPLATE_PATH.read_text(encoding="utf-8")

    def test_CJKの任意位置改行をkeep_allで止めている(self) -> None:
        self.assertIn("word-break: keep-all;", self.css)

    def test_許可点が無い長い文字列はoverflow_wrapで折る(self) -> None:
        self.assertIn("overflow-wrap: anywhere;", self.css)

    def test_対応ブラウザ向けにauto_phraseを残している(self) -> None:
        self.assertIn("word-break: auto-phrase;", self.css)

    def test_keep_allよりも後にauto_phraseを書いている(self) -> None:
        """順序が逆だと、対応ブラウザで auto-phrase が上書きされて効かない。"""

        self.assertLess(
            self.css.index("word-break: keep-all;"),
            self.css.index("word-break: auto-phrase;"),
        )

    def test_base_htmlがwbrをDOM操作で挿入する(self) -> None:
        self.assertIn("createElement('wbr')", self.template)
        self.assertIn("splitText", self.template)

    def test_wbrの挿入対象に本文クラスが含まれる(self) -> None:
        for selector in (".ps", ".empty", ".attention-meta", ".kpi-s"):
            self.assertIn(selector, self.template)

    def test_wbrの挿入は冪等である(self) -> None:
        """再初期化で二重に入らないよう、処理済みの印を持つこと。"""

        self.assertIn("data-wbr", self.template)
