"""コーチマークと説明ツールチップの共通部品。

部品そのものの不変条件をここで固定する。画面ごとの配置は各アプリのテストで見る。
"""

from __future__ import annotations

import re

from django.template import engines
from django.test import SimpleTestCase

HINT = 'partials/hint.html'
COACH = 'partials/coachmark.html'


def render(template_source: str) -> str:
    return engines["django"].from_string(template_source).render({})


class HintTests(SimpleTestCase):
    def test_説明文が読み上げ名に載る(self):
        html = render(
            '{% include "' + HINT + '" with label="確信度" body="入力の品質から決めます。" %}'
        )
        self.assertIn("確信度の説明。入力の品質から決めます。", html)

    def test_同じ画面に複数置いてもidが衝突しない(self):
        """日本語ラベルは slugify が空になる。id で参照する実装に戻さない。"""

        html = render(
            '{% include "' + HINT + '" with label="確信度" body="A" %}'
            '{% include "' + HINT + '" with label="算定不能" body="B" %}'
        )
        ids = re.findall(r'id="([^"]*)"', html)

        self.assertEqual(len(ids), len(set(ids)), f"id が重複している: {ids}")

    def test_見えるツールチップは読み上げから外す(self):
        html = render('{% include "' + HINT + '" with label="スコア" body="影響×確率" %}')

        self.assertIn('class="hint-body" aria-hidden="true"', html)

    def test_JavaScriptに依存しない(self):
        html = render('{% include "' + HINT + '" with label="スコア" body="影響×確率" %}')

        self.assertNotIn("onclick", html)
        self.assertNotIn("<script", html)


class CoachmarkTests(SimpleTestCase):
    def test_1行で書けば描画される(self):
        html = render(
            '{% include "' + COACH + '" with coach_key="x" step1="最初に見る場所" step2="次にする操作" %}'
        )

        self.assertIn('data-coach="x"', html)
        self.assertIn("最初に見る場所", html)
        self.assertIn("次にする操作", html)

    def test_複数行で書くと描画されないので画面側は1行で書く(self):
        """Django の `tag_re` は DOTALL を付けていない（`django/template/base.py`）。

        改行をまたぐ `{% %}` はタグとして認識されず、原文がそのまま画面へ出る。
        この性質は Django 側の仕様なので、こちらは「1 行で書く」を守るしかない。
        """

        html = render(
            '{% include "' + COACH + '" with coach_key="x"\n   step1="最初に見る場所" %}'
        )

        self.assertNotIn('data-coach="x"', html)

    def test_画面テンプレートに複数行の呼び出しが残っていない(self):
        import pathlib

        offenders = []
        for path in pathlib.Path("templates").rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            for marker in ('{% include "partials/coachmark.html"', '{% include "partials/hint.html"'):
                start = text.find(marker)
                while start != -1:
                    end = text.find("%}", start + 2)
                    if end != -1 and "\n" in text[start:end]:
                        offenders.append(str(path))
                        break
                    start = text.find(marker, start + 1)

        self.assertEqual(sorted(set(offenders)), [], "複数行で書かれた呼び出しは描画されない")

    def test_閉じる操作がある(self):
        html = render('{% include "' + COACH + '" with coach_key="x" step1="a" %}')

        self.assertIn("data-coach-close", html)
        self.assertIn("この案内を閉じる", html)

    def test_未指定のステップは出さない(self):
        html = render('{% include "' + COACH + '" with coach_key="x" step1="a" %}')

        self.assertEqual(html.count("<li>"), 1)

    def test_画面を覆わない(self):
        """オーバーレイにすると作業が止まる。帯として本文の前に置く。"""

        html = render('{% include "' + COACH + '" with coach_key="x" step1="a" %}')

        self.assertNotIn("position: fixed", html)
        self.assertNotIn("role=\"dialog\"", html)
