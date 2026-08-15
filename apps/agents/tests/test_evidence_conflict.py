"""根拠間の矛盾検出と、実行計画の正直さのテスト。

矛盾検出が常に False を返す実装は、承認ゲートの「根拠間に矛盾があります」を
永久に到達不能にする。ここでは食い違いを実際に検出できること、そして
**検出できない書き方を「矛盾なし」と言い切らないこと**を確かめる。
"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.agents.models import AgentRun, Recommendation
from apps.agents.services import evidence as evidence_service
from apps.agents.services import orchestrator
from apps.agents.services.intent import classify


class _FakeChunk:
    def __init__(self, document_id: int, text: str = "") -> None:
        self.document_id = document_id
        self.text = text


class _FakeHit:
    def __init__(self, document_id: int, score: float, text: str = "") -> None:
        self.chunk = _FakeChunk(document_id, text)
        self.final_score = score


class ConflictDetectionTests(TestCase):
    def test_同じ項目に別の数値があれば矛盾として検出する(self):
        hits = [
            _FakeHit(1, 0.5, "結合試験の進捗率は 60% で推移している。"),
            _FakeHit(2, 0.5, "結合試験の進捗率は 45% にとどまる。"),
        ]

        conflicts = evidence_service.detect_conflicts(hits)

        self.assertTrue(conflicts)
        self.assertIn("進捗率", conflicts[0])

    def test_相反する状態が書かれていれば矛盾として検出する(self):
        hits = [
            _FakeHit(1, 0.5, "基本設計は完了。"),
            _FakeHit(2, 0.5, "基本設計は未完了のため、レビューを継続する。"),
        ]

        conflicts = evidence_service.detect_conflicts(hits)

        self.assertTrue(conflicts)
        self.assertIn("基本設計", conflicts[0])

    def test_同じ文書の中の推移は矛盾にしない(self):
        # 1 つの文書に「先月 40%／今月 60%」と並ぶのは経過であって食い違いではない。
        hits = [_FakeHit(1, 0.5, "進捗率は 40% だった。進捗率は 60% になった。")]

        self.assertEqual(evidence_service.detect_conflicts(hits), [])

    def test_同じ値なら矛盾にしない(self):
        hits = [
            _FakeHit(1, 0.5, "未解決の課題は 3件。"),
            _FakeHit(2, 0.5, "未解決の課題は 3件のまま。"),
        ]

        self.assertEqual(evidence_service.detect_conflicts(hits), [])

    def test_別項目の数値を突き合わせない(self):
        hits = [
            _FakeHit(1, 0.5, "未解決の課題は 3件。"),
            _FakeHit(2, 0.5, "未クローズの不具合は 7件。"),
        ]

        self.assertEqual(evidence_service.detect_conflicts(hits), [])

    def test_矛盾があれば根拠評価に反映される(self):
        hits = [
            _FakeHit(1, 0.5, "結合試験の進捗率は 60% で推移している。"),
            _FakeHit(2, 0.5, "結合試験の進捗率は 45% にとどまる。"),
        ]

        result = evidence_service.evaluate(hits, classify("結合試験の進捗を確認したい"))

        self.assertTrue(result.has_conflict)
        self.assertTrue(result.conflicts)
        self.assertIn("食い違い", result.notes)

    def test_食い違いが無ければ矛盾ありにしない(self):
        hits = [_FakeHit(index, 0.5, "結合試験は計画どおり進んでいる。") for index in range(1, 6)]

        result = evidence_service.evaluate(hits, classify("結合試験の進捗を確認したい"))

        self.assertFalse(result.has_conflict)
        self.assertEqual(result.conflicts, [])
        self.assertEqual(result.recommendation, Recommendation.ANSWER)

    def test_本文を持たない根拠では矛盾を検出しない(self):
        # 検出できないものを「矛盾あり」にしない（承認ブロックの根拠に使わない）。
        result = evidence_service.evaluate(
            [_FakeHit(1, 0.5), _FakeHit(2, 0.5)], classify("進捗が遅延しています")
        )

        self.assertFalse(result.has_conflict)
        self.assertEqual(result.conflicts, [])


class PlanHonestyTests(TestCase):
    """計画に載せるツールは、実際に実行するものだけであること。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")

    def test_未実装のリランクは計画に載せない(self):
        plan = orchestrator.build_plan(classify("仕様変更の影響を整理したい"), "仕様変更の影響")

        self.assertNotIn("rerank_results", plan.tools)

    def test_計画のツールはすべて実行され記録される(self):
        result = orchestrator.run(
            tenant=self.tenant,
            question="結合試験が遅れています",
            area=AgentRun.Area.PMO_CONSULTATION,
        )
        executed = set(result.run.steps.values_list("tool_name", flat=True))

        for tool in result.run.plan["tools"]:
            with self.subTest(tool=tool):
                self.assertIn(tool, executed | {"expand_query"})

    def test_LLMが使える環境でもリランクは計画に入らない(self):
        # 「使ったように見える」状態を作らない。実装したら _EXECUTED_TOOLS へ足す。
        with self.settings(AI_PROVIDER="openai", OPENAI={**self.settings_openai()}):
            plan = orchestrator.build_plan(classify("進捗が遅延しています"), "進捗")

        self.assertNotIn("rerank_results", plan.tools)

    def settings_openai(self) -> dict:
        from django.conf import settings

        return {**settings.OPENAI, "API_KEY": "test-key"}
