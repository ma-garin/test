"""評価基盤の回帰テスト（traceability #68〜#71）。

外部 API は呼ばない。指標は手計算と一致することを固定し、
「Golden 0 件で Recall 100%」のような嘘が出ないことを保証する。
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.documents.models import Document, DocumentPage, FileType
from apps.rag.models import EvaluationRun, EvaluationSuite, GoldenQuestion, VectorIndex
from apps.rag.services.evaluation import metric_deltas, run_evaluation
from apps.rag.services.evaluation.metrics import aggregate, score_ranking
from apps.rag.services.indexer import rebuild_index


class MetricsTests(TestCase):
    """指標そのものの手計算一致。ここが崩れると全ての数字が信用できない。"""

    def test_1位で命中すれば逆順位は1になる(self):
        metrics = score_ranking(["a"], ["a", "b"], top_k=2)

        self.assertEqual(metrics.recall, 1.0)
        self.assertEqual(metrics.precision, 0.5)
        self.assertEqual(metrics.reciprocal_rank, 1.0)
        self.assertEqual(metrics.first_hit_rank, 1)

    def test_期待文書が出なければ0点になる(self):
        metrics = score_ranking(["z"], ["a", "b"], top_k=2)

        self.assertEqual(metrics.recall, 0.0)
        self.assertEqual(metrics.reciprocal_rank, 0.0)
        self.assertIsNone(metrics.first_hit_rank)
        self.assertEqual(metrics.missing, ("z",))

    def test_3件の平均が手計算と一致する(self):
        # recall = (1 + 0.5 + 0) / 3 = 0.5、MRR = (1 + 1 + 0) / 3 = 0.6667
        cases = [
            score_ranking(["a"], ["a"], top_k=8),
            score_ranking(["b", "c"], ["b"], top_k=8),
            score_ranking(["c"], ["a"], top_k=8),
        ]
        summary = aggregate(cases)

        self.assertTrue(summary.evaluable)
        self.assertAlmostEqual(summary.recall_at_k, 0.5, places=4)
        self.assertAlmostEqual(summary.mrr, 2 / 3, places=4)

    def test_0件は評価不能になる(self):
        summary = aggregate([])

        self.assertFalse(summary.evaluable)
        self.assertIsNone(summary.recall_at_k)
        self.assertIn("0 件", summary.reason)


class EvaluationBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.index = VectorIndex.objects.create(tenant=self.tenant)
        self.test_doc = self._document(
            "テスト管理標準",
            "結合試験の進捗管理では、消化率と不具合収束状況から完了判定を行う。",
        )
        self.expense_doc = self._document(
            "経費精算マニュアル",
            "経費精算は毎月末日までに申請し、上長承認を得ること。",
        )
        self.purchase_doc = self._document(
            "調達ガイド",
            "発注は購買部門の承認を経て行う。",
        )
        rebuild_index(self.index)

    def _document(self, title: str, body: str) -> Document:
        document = Document.objects.create(
            tenant=self.tenant,
            title=title,
            file="dummy.pdf",
            file_type=FileType.PDF,
        )
        DocumentPage.objects.create(document=document, page_number=1, content=body)

        return document

    def _golden(self, question: str, documents: list[Document], **kwargs) -> GoldenQuestion:
        golden = GoldenQuestion.objects.create(tenant=self.tenant, question=question, **kwargs)
        golden.expected_documents.set(documents)
        golden.sync_expected_snapshot()

        return golden


class RetrievalEvaluationTests(EvaluationBase):
    """#68 / #69。語彙のみのスイートは Embedding を一切呼ばない。"""

    def _three_goldens(self) -> None:
        self._golden("結合試験の完了判定", [self.test_doc])
        self._golden("経費精算の申請", [self.expense_doc, self.purchase_doc])
        self.third = self._golden("結合試験の完了判定", [self.purchase_doc])

    def test_Golden3件のRecallとMRRが手計算と一致する(self):
        self._three_goldens()

        run = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.RETRIEVAL_OFFLINE)

        # 1問目: 期待1件中1件命中(1.0) / 2問目: 期待2件中1件命中(0.5) / 3問目: 命中なし(0.0)
        self.assertTrue(run.evaluable)
        self.assertEqual(run.case_count, 3)
        self.assertAlmostEqual(run.recall_at_k, 0.5, places=4)
        self.assertAlmostEqual(run.mrr, 2 / 3, places=4)

    def test_出なかった期待文書は検出事項に残る(self):
        self._three_goldens()

        run = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.RETRIEVAL_OFFLINE)

        self.assertTrue(any("調達ガイド" in issue for issue in run.issues))

    def test_Golden0件は評価不能になる(self):
        run = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.RETRIEVAL_OFFLINE)

        self.assertFalse(run.evaluable)
        self.assertIsNone(run.recall_at_k)
        self.assertIsNone(run.mrr)
        self.assertIn("0 件", run.unavailable_reason)

    def test_期待文書が削除されると検知され採点しない(self):
        self._golden("結合試験の完了判定", [self.test_doc])
        self.test_doc.soft_delete()

        run = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.RETRIEVAL_OFFLINE)
        case = run.cases.first()

        self.assertTrue(any("削除済み" in issue for issue in run.issues))
        self.assertFalse(case.evaluable)
        self.assertIsNone(case.recall)

    def test_前回結果との差分が出る(self):
        self._three_goldens()
        run_evaluation(tenant=self.tenant, suite=EvaluationSuite.RETRIEVAL_OFFLINE)

        # 3問目（命中なし）を無効化すると Recall は 0.5 → 0.75 へ改善するはず。
        self.third.is_active = False
        self.third.save(update_fields=["is_active"])
        latest = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.RETRIEVAL_OFFLINE)

        recall_row = next(row for row in metric_deltas(latest) if row.key == "recall_at_k")

        self.assertEqual(recall_row.current, 75.0)
        self.assertEqual(recall_row.previous, 50.0)
        self.assertEqual(recall_row.delta, 25.0)
        self.assertEqual(recall_row.tone, "g")

    def test_ハイブリッド検索でも実行できる(self):
        self._golden("結合試験の完了判定", [self.test_doc])

        run = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.RETRIEVAL)

        self.assertTrue(run.evaluable)
        self.assertEqual(run.metrics["use_vector"], True)


class AnswerDryRunTests(EvaluationBase):
    """#70。生成物を保存せず、引用・必須セクション・抑制だけを見る。"""

    def test_根拠がある質問は引用付きで合格する(self):
        self._golden("結合試験の完了判定", [self.test_doc])

        run = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.ANSWER)
        case = run.cases.first()

        self.assertTrue(run.evaluable)
        self.assertTrue(case.passed)
        self.assertGreater(case.detail["citations"], 0)

    def test_抑制が期待される質問で断定すると不合格になる(self):
        self._golden("結合試験の完了判定", [self.test_doc], must_abstain=True)

        run = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.ANSWER)

        self.assertEqual(run.pass_rate, 0.0)
        self.assertTrue(any("抑制" in issue for issue in run.issues))

    def test_dry_runは回答を保存しない(self):
        self._golden("結合試験の完了判定", [self.test_doc])

        run_evaluation(tenant=self.tenant, suite=EvaluationSuite.ANSWER)

        from apps.rag.models import RagAnswer

        self.assertEqual(RagAnswer.objects.count(), 0)

    def test_必須セクションの欠落を検知する(self):
        self._golden("結合試験の完了判定", [self.test_doc], required_sections=["未確認事項"])

        run = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.ANSWER)

        self.assertTrue(any("必須セクション" in issue for issue in run.issues))


class StaticCheckTests(EvaluationBase):
    """#71。検索せずに索引と Golden の整合だけを見る。"""

    def test_健全な索引では検出事項が出ない(self):
        self._golden("結合試験の完了判定", [self.test_doc])

        run = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.STATIC)

        self.assertTrue(run.evaluable)
        self.assertEqual(run.issues, [])
        self.assertEqual(run.metrics["chunks"], run.metrics["vectors"])

    def test_削除済み文書のチャンクが残っていると検知する(self):
        self._golden("結合試験の完了判定", [self.test_doc])
        self.test_doc.soft_delete()

        run = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.STATIC)

        self.assertTrue(any("削除済み" in issue for issue in run.issues))

    def test_Golden0件は検出事項として報告する(self):
        run = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.STATIC)

        self.assertTrue(any("Golden" in issue for issue in run.issues))

    def test_期待文書の参照が失われると検知する(self):
        golden = self._golden("結合試験の完了判定", [self.test_doc])
        golden.expected_documents.clear()

        run = run_evaluation(tenant=self.tenant, suite=EvaluationSuite.STATIC)

        self.assertTrue(any("参照が失われ" in issue for issue in run.issues))


class RunEvaluationCommandTests(EvaluationBase):
    """管理コマンドと画面が同じ経路を通ることを固定する。"""

    def test_コマンドから実行できる(self):
        self._golden("結合試験の完了判定", [self.test_doc])
        out = StringIO()

        call_command(
            "run_evaluation",
            "--tenant",
            "acme",
            "--suite",
            EvaluationSuite.RETRIEVAL_OFFLINE,
            stdout=out,
        )

        self.assertEqual(EvaluationRun.objects.count(), 1)
        self.assertIn("Recall@K", out.getvalue())

    def test_Golden0件なら評価不能と表示する(self):
        out = StringIO()

        call_command("run_evaluation", "--tenant", "acme", stdout=out)

        self.assertIn("評価不能", out.getvalue())
        self.assertNotIn("Recall@K", out.getvalue())

    def test_存在しないテナントはエラーになる(self):
        with self.assertRaises(CommandError):
            call_command("run_evaluation", "--tenant", "missing")


class EvaluationViewTests(EvaluationBase):
    def setUp(self) -> None:
        super().setUp()
        self.client.force_login(self.user)
        self.url = reverse("rag:evaluation")

    def test_未ログインならリダイレクトする(self):
        self.client.logout()

        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_画面が200を返す(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "指標の定義")
        self.assertContains(response, "Recall@K")

    def test_Golden0件では評価不能と表示する(self):
        self.client.post(self.url, {"suite": EvaluationSuite.RETRIEVAL})
        response = self.client.get(self.url)

        self.assertContains(response, "評価不能")
        self.assertNotContains(response, "kpi-v")

    def test_実行すると履歴が残る(self):
        self._golden("結合試験の完了判定", [self.test_doc])

        response = self.client.post(self.url, {"suite": EvaluationSuite.RETRIEVAL_OFFLINE})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(EvaluationRun.objects.filter(tenant=self.tenant).count(), 1)

    def test_画面からGoldenを登録できる(self):
        response = self.client.post(
            self.url,
            {
                "action": "add_golden",
                "question": "完了判定の基準は？",
                "category": "テスト管理",
                "expected_documents": [str(self.test_doc.pk)],
                "expected_terms": "完了判定、消化率",
            },
        )
        golden = GoldenQuestion.objects.get(question="完了判定の基準は？")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(golden.expected_terms, ["完了判定", "消化率"])
        self.assertEqual(golden.expected_document_titles, ["テスト管理標準"])

    def test_質問が空ならエラーを返し登録しない(self):
        response = self.client.post(self.url, {"action": "add_golden", "question": "  "})

        self.assertEqual(GoldenQuestion.objects.count(), 0)
        self.assertIn("error=question", response["Location"])

    def test_他テナントのGoldenは見えない(self):
        other = Tenant.objects.create(code="other", name="OTHER")
        GoldenQuestion.objects.create(tenant=other, question="他社の質問です")

        response = self.client.get(self.url)

        self.assertNotContains(response, "他社の質問です")
