"""テンプレートの静的検査。

`{# #}` を複数行に書くと、Django はコメントとして扱わず**本文として描画する**。
この不具合はこのリポジトリで 2 回起きている（1 回目は実データ投入時の修正、
2 回目はファビコン追加時）。人の注意力ではなく検査で止める。

ブラウザテスト（`test_e2e_flows.py`）でも表面化するが、E2E は重い。
書いた直後に落ちるよう、こちらは実行が速い静的検査として置く。
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase

TEMPLATE_ROOT = Path(settings.BASE_DIR) / "templates"

#: 開始タグ。対応する `#}` が同じ行に無ければ描画されてしまう。
COMMENT_OPEN = re.compile(r"\{#")


def _template_files() -> list[Path]:
    return sorted(TEMPLATE_ROOT.rglob("*.html"))


class TemplateHygieneTests(TestCase):
    def test_複数行のコメントを書いていない(self) -> None:
        """`{# #}` は単一行のみ。複数行にしたい場合は `{% comment %}` を使う。"""

        offenders: list[str] = []

        for path in _template_files():
            source = path.read_text(encoding="utf-8")

            for match in COMMENT_OPEN.finditer(source):
                rest = source[match.start() :]
                close = rest.find("#}")

                if close == -1 or "\n" in rest[:close]:
                    line = source[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(TEMPLATE_ROOT)}:{line}")

        self.assertEqual(
            offenders,
            [],
            "複数行の {# #} は画面へ描画されます。{% comment %} を使ってください: "
            + ", ".join(offenders),
        )

    def test_テンプレートが1件以上見つかる(self) -> None:
        """検査対象が 0 件のまま「合格」になるのを防ぐ。"""

        self.assertGreater(len(_template_files()), 0)

    def test_閉じ忘れたブロックタグが無い(self) -> None:
        """`{% block %}` と `{% endblock %}` の数を数える。

        Django は読み込み時に例外にするが、その画面を開くまで気づけない。
        全テンプレートをまとめてここで見る。
        """

        offenders: list[str] = []

        for path in _template_files():
            source = path.read_text(encoding="utf-8")

            for tag in ("block", "if", "for", "comment", "with"):
                opens = len(re.findall(r"\{%\s*" + tag + r"[\s%]", source))
                closes = len(re.findall(r"\{%\s*end" + tag + r"\s*%\}", source))

                if opens != closes:
                    offenders.append(
                        f"{path.relative_to(TEMPLATE_ROOT)}: {tag} が {opens} 個、"
                        f"end{tag} が {closes} 個"
                    )

        self.assertEqual(offenders, [], "開始タグと終了タグの数が合いません: " + str(offenders))
