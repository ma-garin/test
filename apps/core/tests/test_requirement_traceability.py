"""要件とテストのトレーサビリティ検査。

INCIDENT-001 の教訓は「実装側の申告を充足判定に使わない」だった。
同じことがテストでも起きている。「テスト 1,284 件が通った」は**件数の申告**で
あって、要件を担保している証拠ではない。

そこで、要件（`docs/requirements/traceability.md` の 74 項目）と、
それを担保するテストの対応を `REQUIREMENT_TESTS` に明示し、次を検査する。

1. 「済」の要件には、担保するテストが必ず割り当てられていること
2. 割り当てたテストモジュールが**実在**し、テストを含んでいること
3. 対応表に、要件表に無い番号が紛れていないこと

**この対応表は人が書く。** 自動生成にすると「テストがある要件だけを要件と呼ぶ」
ことになり、分母を実装側で決めるという INCIDENT-001 と同じ誤りを繰り返す。
"""

from __future__ import annotations

import importlib
import re
import unittest
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.test import TestCase

TRACEABILITY = Path(settings.BASE_DIR) / "docs" / "requirements" / "traceability.md"

#: 要件番号 → それを担保するテスト（モジュール名の列）。
#: 「画面が開く」だけでは担保にならないため、業務ロジックを見ているものを挙げる。
REQUIREMENT_TESTS: dict[int, tuple[str, ...]] = {
    1: ("apps.dashboard.tests.test_views",),
    2: ("apps.dashboard.tests.test_views",),
    3: ("apps.dashboard.tests.test_poc_evaluation",),
    4: ("apps.dashboard.tests.test_milestones",),
    5: ("apps.dashboard.tests.test_detection",),
    6: ("apps.dashboard.tests.test_views",),
    7: ("apps.dashboard.tests.test_detection",),
    8: ("apps.dashboard.tests.test_poc_evaluation",),
    9: ("apps.dashboard.tests.test_views",),
    10: ("apps.pmo.tests.test_generators",),
    11: ("apps.pmo.tests.test_generators",),
    12: ("apps.pmo.tests.test_generators",),
    13: ("apps.pmo.tests.test_generators",),
    14: ("apps.pmo.tests.test_views",),
    15: ("apps.pmo.tests.test_fact_check",),
    16: ("apps.pmo.tests.test_approval",),
    17: ("apps.rag.tests.test_business_rag",),
    18: ("apps.rag.tests.test_business_rag",),
    19: ("apps.integrations.tests.test_confluence",),
    20: ("apps.rag.tests.test_retrieval",),
    21: ("apps.rag.tests.test_chat",),
    22: ("apps.core.tests.test_screen_context",),
    23: ("apps.agents.tests.test_orchestrator",),
    24: ("apps.agents.tests.test_orchestrator",),
    25: ("apps.integrations.tests.test_external_links",),
    26: ("apps.agents.tests.test_orchestrator",),
    27: ("apps.rag.tests.test_retrieval",),
    28: ("apps.accounts.tests.test_login",),
    29: ("apps.core.tests.test_project_scope",),
    30: ("apps.projects.tests.test_project_permissions",),
    31: ("apps.integrations.tests.test_jira_connector",),
    32: ("apps.integrations.tests.test_notify",),
    33: ("apps.integrations.tests.test_confluence",),
    34: ("apps.integrations.tests.test_git",),
    35: ("apps.integrations.tests.test_pipeline",),
    36: ("apps.integrations.tests.test_pipeline",),
    37: ("apps.integrations.tests.test_sync",),
    38: (),  # 対象外（参考データで「テキスト上も対象外」）
    39: ("apps.dashboard.tests.test_detection",),
    40: ("apps.dashboard.tests.test_detection",),
    41: ("apps.dashboard.tests.test_detection",),
    42: ("apps.pmo.tests.test_generators",),
    43: ("apps.pmo.tests.test_generators",),
    44: ("apps.documents.tests.test_requirement_coverage",),
    45: ("apps.rag.tests.test_retrieval",),
    46: ("apps.integrations.tests.test_confluence",),
    47: ("apps.dashboard.tests.test_input_rules",),
    48: ("apps.pmo.tests.test_approval",),
    49: ("apps.pmo.tests.test_approval",),
    50: ("apps.dashboard.tests.test_poc_evaluation",),
    51: ("apps.dashboard.tests.test_poc_evaluation",),
    52: ("apps.dashboard.tests.test_poc_evaluation",),
    53: ("apps.dashboard.tests.test_poc_evaluation",),
    54: ("apps.dashboard.tests.test_poc_evaluation",),
    55: ("apps.documents.tests.test_extractors",),
    56: ("apps.documents.tests.test_documents",),
    57: ("apps.rag.tests.test_retrieval",),
    58: ("apps.rag.tests.test_retrieval",),
    59: ("apps.rag.tests.test_chat",),
    60: ("apps.rag.tests.test_business_rag",),
    61: ("apps.documents.tests.test_documents",),
    62: ("apps.documents.tests.test_template_export",),
    63: ("apps.pmo.tests.test_views",),
    64: ("apps.pmo.tests.test_generators",),
    65: ("apps.agents.tests.test_orchestrator",),
    66: ("apps.dashboard.tests.test_intervention_decision",),
    67: ("apps.core.tests.test_seed_demo",),
    68: ("apps.rag.tests.test_evaluation",),
    69: ("apps.rag.tests.test_evaluation",),
    70: ("apps.rag.tests.test_evaluation",),
    71: ("apps.rag.tests.test_evaluation",),
    72: ("apps.audit.tests.test_feedback_submit",),
    73: ("apps.core.tests.test_views",),
    74: ("apps.core.tests.test_views",),
}

#: 要件表に載らない横断的な検証。要件番号は付かないが、無くなると困る。
CROSS_CUTTING_TESTS = (
    "apps.core.tests.test_screen_scenarios",
    "apps.core.tests.test_e2e_flows",
    "apps.core.tests.test_query_budget",
    "apps.core.tests.test_template_hygiene",
    "apps.core.tests.test_open_question_ids",
    # 実データ投入。要件表の行には対応しないが、充足率の写しを検査している。
    "apps.core.tests.test_seed_dev_project",
    "apps.projects.tests.test_form_boundaries",
)

ROW = re.compile(r"^\|\s*(\d+)\s*\|.*\|\s*\*\*(済|部分|未|対象外)\*\*\s*\|")

#: 「## 集計」の表。状態ごとの件数を人が書くため、行と食い違ったことがある。
SUMMARY_ROW = re.compile(r"^\|\s*\*{0,2}(済|部分|未|実装中|対象外|合計)\*{0,2}\s*\|\s*\*{0,2}(\d+)")


def _requirement_states() -> dict[int, str]:
    """突合表から「番号 → 状態」を読む。分母は必ずこのファイルから取る。"""

    states: dict[int, str] = {}

    for line in TRACEABILITY.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line.strip())

        if match:
            states[int(match.group(1))] = match.group(2)

    return states


def _summary_counts() -> dict[str, int]:
    """「## 集計」に書かれた件数を読む。"""

    text = TRACEABILITY.read_text(encoding="utf-8")
    section = text.split("## 集計")[-1].split("## 残っているもの")[0]
    counts: dict[str, int] = {}

    for line in section.splitlines():
        match = SUMMARY_ROW.match(line.strip())

        if match:
            counts[match.group(1)] = int(match.group(2))

    return counts


def _load_tests(module_name: str) -> int:
    module = importlib.import_module(module_name)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)

    return suite.countTestCases()


class RequirementTraceabilityTests(TestCase):
    def setUp(self) -> None:
        self.states = _requirement_states()

    def test_突合表を読み取れる(self) -> None:
        """表の形式が変わって 0 件になったまま「合格」になるのを防ぐ。"""

        self.assertGreaterEqual(len(self.states), 70, "突合表の行を読み取れていない")

    def test_集計が表の行と一致する(self) -> None:
        """集計値を人が書いているため、行を足し引きすると実際にずれる。

        以前「済63／部分5／未7／合計76」と書かれていたが、
        表の行数は74だった。分母が違えば充足率も違う。
        """

        summary = _summary_counts()
        counted = Counter(self.states.values())

        self.assertTrue(summary, "「## 集計」の表を読み取れていない")

        for label, written in summary.items():
            with self.subTest(state=label):
                actual = len(self.states) if label == "合計" else counted.get(label, 0)

                self.assertEqual(
                    written,
                    actual,
                    f"集計の「{label}」は {written} 件だが、表の行を数えると {actual} 件",
                )

    def test_済の要件にはテストが割り当てられている(self) -> None:
        missing = [
            number
            for number, state in self.states.items()
            if state == "済" and not REQUIREMENT_TESTS.get(number)
        ]

        self.assertEqual(missing, [], f"「済」だがテストの割り当てが無い要件: {missing}")

    def test_割り当てたテストが実在する(self) -> None:
        broken: list[str] = []

        for number, modules in REQUIREMENT_TESTS.items():
            for module_name in modules:
                try:
                    count = _load_tests(module_name)
                except ModuleNotFoundError:
                    broken.append(f"要件{number}: {module_name} が存在しない")
                    continue

                if count == 0:
                    broken.append(f"要件{number}: {module_name} にテストが無い")

        self.assertEqual(broken, [], f"割り当てが壊れている: {broken}")

    def test_対応表に要件表に無い番号が無い(self) -> None:
        unknown = sorted(set(REQUIREMENT_TESTS) - set(self.states))

        self.assertEqual(unknown, [], f"突合表に無い要件番号が対応表にある: {unknown}")

    def test_対応表が全要件を覆う(self) -> None:
        uncovered = sorted(set(self.states) - set(REQUIREMENT_TESTS))

        self.assertEqual(uncovered, [], f"対応表に無い要件がある: {uncovered}")

    def test_横断的なテストが残っている(self) -> None:
        """画面シナリオ・E2E・性能・静的検査を消したら気づけるようにする。"""

        for module_name in CROSS_CUTTING_TESTS:
            with self.subTest(module=module_name):
                self.assertGreater(_load_tests(module_name), 0, f"{module_name} が空")

    def test_残っている要件は突合表で説明されている(self) -> None:
        """「部分」「未」が残っているなら、突合表の残作業欄に理由が書かれていること。"""

        pending = [number for number, state in self.states.items() if state in ("部分", "未")]
        remaining = TRACEABILITY.read_text(encoding="utf-8").split("## 残っているもの")[-1]

        for number in pending:
            with self.subTest(requirement=number):
                self.assertIn(
                    f"| {number} |",
                    remaining,
                    f"要件{number} が「残っているもの」に説明されていない",
                )
