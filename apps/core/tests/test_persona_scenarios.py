"""ペルソナ別シナリオ台帳の検査。

台帳そのもの（`persona_catalog.py`）が壊れていないことを確かめる。
シナリオの中身を実行するのは既存のテスト群（画面シナリオ 623 件、
E2E 13 件、境界値 40 件ほか）で、ここはその**対応表が欠けていないか**を見る。

台帳を「作って終わり」にすると、画面を足したときに黙って穴が空く。
`SCREENS` を唯一の出所にしているので、画面が増えれば件数が増え、
ここの期待値と合わなくなって落ちる。
"""

from __future__ import annotations

from django.test import TestCase

from apps.core.tests.persona_catalog import (
    AUTO,
    GAP,
    MANUAL,
    PER_PERSONA,
    PERSONAS,
    SCREEN_META,
    SPECIFICS,
    build_scenarios,
)
from apps.core.tests.test_screen_scenarios import SCREENS


class PersonaCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.scenarios = build_scenarios()

    def test_件数が画面数掛けるペルソナ数掛ける10になる(self) -> None:
        expected = len(SCREENS) * len(PERSONAS) * PER_PERSONA

        self.assertEqual(len(self.scenarios), expected)
        self.assertEqual(len(self.scenarios), 2600)

    def test_全画面が台帳に載っている(self) -> None:
        covered = {scenario.screen for scenario in self.scenarios}

        self.assertEqual(covered, {screen.url_name for screen in SCREENS})

    def test_全画面に日本語の呼び名がある(self) -> None:
        """呼び名が無いと URL 名がそのまま資料へ出て、読み手に伝わらない。"""

        missing = [
            screen.url_name for screen in SCREENS if screen.url_name not in SCREEN_META
        ]

        self.assertEqual(missing, [], f"呼び名が未定義の画面: {missing}")

    def test_画面ごとにペルソナ4人分そろっている(self) -> None:
        by_screen: dict[str, set[str]] = {}

        for scenario in self.scenarios:
            by_screen.setdefault(scenario.screen, set()).add(scenario.persona)

        expected = {persona for persona, _, _, _ in PERSONAS}

        for screen, personas in by_screen.items():
            with self.subTest(screen=screen):
                self.assertEqual(personas, expected)

    def test_ペルソナごとに10案ある(self) -> None:
        counts: dict[tuple[str, str], int] = {}

        for scenario in self.scenarios:
            key = (scenario.screen, scenario.persona)
            counts[key] = counts.get(key, 0) + 1

        for key, count in counts.items():
            with self.subTest(key=key):
                self.assertEqual(count, PER_PERSONA)

    def test_シナリオIDが一意(self) -> None:
        ids = [scenario.scenario_id for scenario in self.scenarios]

        self.assertEqual(len(ids), len(set(ids)))

    def test_判定は3種類のいずれか(self) -> None:
        allowed = {AUTO, MANUAL, GAP}

        for scenario in self.scenarios:
            with self.subTest(scenario=scenario.scenario_id):
                self.assertIn(scenario.verdict, allowed)

    def test_雛形の差し込みが残っていない(self) -> None:
        """`{label}` `{noun}` が未展開のまま資料へ出るのを防ぐ。"""

        for scenario in self.scenarios:
            with self.subTest(scenario=scenario.scenario_id):
                self.assertNotIn("{", scenario.story)
                self.assertNotIn("{", scenario.expectation)

    def test_画面固有の観点が実在する画面を指している(self) -> None:
        known = {screen.url_name for screen in SCREENS}
        unknown = sorted({url for url, _, _ in SPECIFICS if url not in known})

        self.assertEqual(unknown, [], f"存在しない画面への固有観点: {unknown}")

    def test_画面固有の観点の番号が範囲内(self) -> None:
        out_of_range = [
            key for key in SPECIFICS if not 0 <= key[2] < PER_PERSONA
        ]

        self.assertEqual(out_of_range, [], f"案の番号が範囲外: {out_of_range}")

    def test_稲妻線のシナリオが自動判定として登録されている(self) -> None:
        """実装した以上、指摘の再発を自動で止められる状態にしておく。"""

        matched = [
            scenario
            for scenario in self.scenarios
            if scenario.screen == "dashboard:tasks" and "稲妻線" in scenario.expectation
        ]

        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].verdict, AUTO)
        self.assertEqual(matched[0].persona, "PM25年")

    def test_未充足がPM25年に偏っている(self) -> None:
        """ペルソナ分割の狙いどおりか。実務観点から欠落が出るはず。"""

        gaps = [s for s in self.scenarios if s.verdict == GAP]
        by_persona: dict[str, int] = {}

        for scenario in gaps:
            by_persona[scenario.persona] = by_persona.get(scenario.persona, 0) + 1

        self.assertGreater(by_persona.get("PM25年", 0), by_persona.get("QA", 0))
