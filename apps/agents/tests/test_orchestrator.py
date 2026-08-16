"""意図分類・根拠評価・オーケストレーターのテスト。

意図分類は旧実装の挙動を保つことが移植の受け入れ条件なので、
代表的な入力に対する分類結果を固定する。
"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.agents.models import AgentRun, Intent, Recommendation
from apps.agents.services import orchestrator
from apps.agents.services.evidence import evaluate
from apps.agents.services.intent import classify
from apps.rag.models import VectorIndex


class IntentClassifyTests(TestCase):
    def test_遅延の相談は進捗遅延に分類される(self):
        result = classify("結合試験が5日遅れています。どうリカバリすべきですか。")

        self.assertEqual(result.intent, Intent.DELAY)
        self.assertEqual(result.confidence_label, "high")

    def test_完了判断はテスト管理として強く分類される(self):
        result = classify("受入試験の完了判断の基準を教えてください")

        self.assertEqual(result.intent, Intent.TEST)

    def test_仕様変更は変更影響に分類される(self):
        self.assertEqual(classify("仕様変更のスコープ影響を整理したい").intent, Intent.CHANGE)

    def test_不具合は品質懸念に分類される(self):
        self.assertEqual(classify("重大不具合が続いています").intent, Intent.QUALITY)

    def test_品質への不安はリスク相談として扱う(self):
        # 対象語と感情語の組み合わせで加点される旧実装のルール。
        self.assertEqual(classify("品質が不安です").intent, Intent.RISK)

    def test_空入力は一般相談かつ低確信度(self):
        result = classify("")

        self.assertEqual(result.intent, Intent.GENERAL)
        self.assertEqual(result.confidence_label, "low")

    def test_該当語がなければ一般相談(self):
        self.assertEqual(classify("こんにちは").intent, Intent.GENERAL)

    def test_意図ごとに確認観点が返る(self):
        self.assertIn("リカバリ策", classify("進捗が遅延しています").viewpoints)


class _FakeChunk:
    def __init__(self, document_id: int) -> None:
        self.document_id = document_id


class _FakeHit:
    def __init__(self, document_id: int, score: float) -> None:
        self.chunk = _FakeChunk(document_id)
        self.final_score = score


class EvidenceEvaluationTests(TestCase):
    def test_根拠なしなら追加確認を求める(self):
        result = evaluate([], classify("進捗が遅延しています"))

        self.assertEqual(result.recommendation, Recommendation.ASK_CLARIFICATION)
        self.assertEqual(result.confidence, 0.36)

    def test_単一文書に偏る場合は注意付き回答(self):
        hits = [_FakeHit(1, 0.5), _FakeHit(1, 0.4)]
        result = evaluate(hits, classify("進捗が遅延しています"))

        self.assertEqual(result.recommendation, Recommendation.ANSWER_WITH_CAUTION)
        self.assertIn("根拠が単一文書に偏っています。他資料での裏取りが必要です", result.missing_information)

    def test_複数文書から十分に取れていれば回答してよい(self):
        hits = [_FakeHit(i, 0.5) for i in range(1, 6)]
        result = evaluate(hits, classify("結合試験が5日遅れています。リカバリを検討したい。"))

        self.assertEqual(result.recommendation, Recommendation.ANSWER)

    def test_スコアが閾値未満の結果は根拠として数えない(self):
        result = evaluate([_FakeHit(1, 0.0001)], classify("進捗が遅延しています"))

        self.assertEqual(result.recommendation, Recommendation.ASK_CLARIFICATION)


class OrchestratorRunTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")

    def test_インデックスなしでも実行が完了しトレースが残る(self):
        result = orchestrator.run(
            tenant=self.tenant,
            question="結合試験が遅れています",
            area=AgentRun.Area.PMO_CONSULTATION,
        )

        self.assertEqual(result.run.status, AgentRun.Status.SUCCEEDED)
        self.assertEqual(result.run.intent, Intent.DELAY)
        self.assertEqual(result.run.steps.count(), 4)
        self.assertEqual(result.run.steps.get(order=3).status, "skipped")

    def test_根拠不足なら承認をブロックする(self):
        result = orchestrator.run(
            tenant=self.tenant,
            question="結合試験が遅れています",
            area=AgentRun.Area.PMO_CONSULTATION,
        )

        self.assertTrue(result.evidence.blocks_approval)

    def test_LLM未設定時は計画にLLM必須ツールを含めない(self):
        # テスト設定は AI_PROVIDER=local_hash なので rerank は計画へ入らない。
        result = orchestrator.run(
            tenant=self.tenant,
            question="仕様変更の影響を整理したい",
            area=AgentRun.Area.PMO_CONSULTATION,
        )

        self.assertNotIn("rerank_results", result.run.plan["tools"])
        self.assertIn("search_local_docs", result.run.plan["tools"])

    def test_ループ回数は上限を超えない(self):
        from django.conf import settings

        result = orchestrator.run(
            tenant=self.tenant,
            question="品質が不安です",
            area=AgentRun.Area.PMO_CONSULTATION,
        )

        self.assertLessEqual(result.run.loop_count, settings.AGENT["MAX_LOOPS"])

    def test_インデックスがあれば検索ステップが成功になる(self):
        index = VectorIndex.objects.create(tenant=self.tenant)
        result = orchestrator.run(
            tenant=self.tenant,
            question="課題の優先順位を整理したい",
            area=AgentRun.Area.PMO_CONSULTATION,
            index=index,
        )

        self.assertEqual(result.run.steps.get(order=3).status, "ok")
