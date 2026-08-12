"""RAG 3 画面の表示規律（UXP-23 / UXP-24 / UXP-25）。

この 3 画面が守るべきことは 1 つに集約される。

    **生成された文を、確定情報と誤認させない。**

そのため次を回帰として固定する。

- 引用が 0 件のときは「根拠なし」と言い切る（弱い肯定で濁さない）
- 引用数・対象範囲・取得時点を、回答の**近く**に置く（別の場所へ逃がさない）
- 悪化した指標と失敗ケースを先に出し、詳細表は後ろへ置く

外部 API は呼ばない（`local_hash`）。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.documents.models import Document, DocumentPage, FileType
from apps.rag.models import EvaluationSuite, GoldenQuestion, VectorIndex
from apps.rag.services.evaluation import run_evaluation
from apps.rag.services.indexer import rebuild_index


class RagScreenBase(TestCase):
    """3 画面共通のテナント・ユーザー・文書。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

    def _document(self, title: str, body: str) -> Document:
        document = Document.objects.create(
            tenant=self.tenant,
            title=title,
            file="dummy.pdf",
            file_type=FileType.PDF,
        )
        DocumentPage.objects.create(document=document, page_number=1, content=body)

        return document

    def _empty_index(self) -> VectorIndex:
        """索引はあるがチャンクが 0 件の状態。検索は必ず 0 件になる。"""

        return VectorIndex.objects.create(tenant=self.tenant)

    def _built_index(self) -> VectorIndex:
        index = self._empty_index()
        self._document(
            "テスト管理標準",
            "結合試験の進捗管理では、消化率と不具合収束状況から完了判定を行う。",
        )
        self._document(
            "品質管理標準",
            "完了判定は不具合の収束傾向と未消化ケースの残量で判断する。",
        )
        rebuild_index(index)

        return index

    def _order(self, response, *needles: str) -> list[int]:
        """本文中の出現位置。並び順そのものを検証するために使う。"""

        body = response.content.decode()
        positions = []

        for needle in needles:
            position = body.find(needle)
            self.assertNotEqual(position, -1, f"画面に「{needle}」がありません")
            positions.append(position)

        return positions


class SearchScreenTests(RagScreenBase):
    """UXP-23: 回答の要点 → 引用元 → スコアの見方。"""

    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("rag:search")

    def test_引用0件なら根拠なしと明示する(self):
        self._empty_index()
        response = self.client.get(self.url, {"q": "結合試験の完了判定"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "根拠なし")
        self.assertContains(response, "引用 0件")
        # 「見つからなかった」を「事実が無い」と読み替えさせない。
        self.assertContains(response, "ことの証明ではありません")

    def test_回答の要点_引用元_スコアの見方の順に並ぶ(self):
        self._built_index()
        response = self.client.get(self.url, {"q": "結合試験の完了判定"})

        summary, citations, scores = self._order(
            response, "1. 回答の要点", "2. 引用元", "3. スコアの見方"
        )

        self.assertLess(summary, citations)
        self.assertLess(citations, scores)

    def test_引用0件でも3つの見出しは同じ順で出る(self):
        self._empty_index()
        response = self.client.get(self.url, {"q": "存在しない語"})

        summary, citations, scores = self._order(
            response, "1. 回答の要点", "2. 引用元", "3. スコアの見方"
        )

        self.assertLess(summary, citations)
        self.assertLess(citations, scores)

    def test_各引用に原文を開く導線がある(self):
        self._built_index()
        response = self.client.get(self.url, {"q": "結合試験の完了判定"})

        self.assertContains(response, "原文を開く")
        self.assertContains(response, reverse("documents:list"))

    def test_対象範囲と取得時点と十分性を要点の中に出す(self):
        self._built_index()
        response = self.client.get(self.url, {"q": "結合試験の完了判定"})

        body = response.content.decode()
        section = body[body.find("1. 回答の要点") : body.find("2. 引用元")]

        # 引用元より前、つまり要点と同じ画面位置で読めることを固定する。
        self.assertIn("対象範囲", section)
        self.assertIn("取得時点", section)
        self.assertIn("根拠の十分性", section)
        self.assertIn("引用 2件", section)

    def test_要点は確定した回答ではないと断る(self):
        self._built_index()
        response = self.client.get(self.url, {"q": "結合試験の完了判定"})

        self.assertContains(response, "確定した回答ではありません")


class ChatScreenTests(RagScreenBase):
    """UXP-24: 応答の直下に対象範囲・引用数・根拠不足。"""

    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("rag:chat")

    def test_根拠が無い応答は根拠なしと引用0件を出す(self):
        self.client.post(self.url, {"message": "結合試験の完了判定はどう決める？"})
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "0件（根拠なし）")
        self.assertContains(response, "確定情報として引用しないでください")

    def test_引用数と対象範囲と根拠不足が応答の直下に出る(self):
        self._built_index()
        self.client.post(self.url, {"message": "結合試験の完了判定はどう決める？"})
        response = self.client.get(self.url)

        bubble, scope_label, citation_count, missing = self._order(
            response, "ai-bubble", "対象範囲", "引用", "根拠不足"
        )

        self.assertLess(bubble, scope_label)
        self.assertLess(scope_label, missing)
        self.assertLess(citation_count, missing + 200)

    def test_未検証の応答は要検証として識別できる(self):
        self.client.post(self.url, {"message": "結合試験の完了判定はどう決める？"})
        response = self.client.get(self.url)

        self.assertContains(response, "要検証")

    def test_根拠不足は折りたたまず応答の近くに出す(self):
        """`details`/`summary` に隠すと読まれない。素の要素で出す。"""

        self.client.post(self.url, {"message": "結合試験の完了判定はどう決める？"})
        response = self.client.get(self.url)
        body = response.content.decode()

        self.assertNotIn("<details", body)
        self.assertLess(body.find("ai-bubble"), body.find("根拠不足"))


class EvaluationScreenTests(RagScreenBase):
    """UXP-25: 悪化指標を最上部、失敗ケースへの絞り込み、詳細表は後ろ。"""

    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("rag:evaluation")
        self.index = self._built_index()
        self.hit_document = Document.objects.get(title="テスト管理標準")

    def _golden(self, question: str, document: Document) -> GoldenQuestion:
        golden = GoldenQuestion.objects.create(tenant=self.tenant, question=question)
        golden.expected_documents.set([document])
        golden.sync_expected_snapshot()

        return golden

    def _run(self) -> None:
        run_evaluation(tenant=self.tenant, suite=EvaluationSuite.RETRIEVAL, user=self.user)

    def _regress(self) -> Document:
        """索引に無い文書を期待する Golden を足し、次の実行を確実に悪化させる。"""

        missing = self._document("未索引の標準", "この文書は索引に含まれない。")
        self._golden("索引に無い文書を引く質問", missing)
        self._run()

        return missing

    def test_悪化した指標を最上部に集約する(self):
        self._golden("結合試験の完了判定は？", self.hit_document)
        self._run()
        self._regress()

        response = self.client.get(self.url)
        regression, form = self._order(response, "前回より悪化した指標", "評価を実行する")

        self.assertLess(regression, form)
        self.assertContains(response, "悪化")

    def test_失敗ケースだけに絞り込める(self):
        self._golden("結合試験の完了判定は？", self.hit_document)
        self._run()
        self._regress()

        response = self.client.get(self.url, {"cases": "failed"})
        questions = [case.question for case in response.context["cases"]]

        self.assertEqual(questions, ["索引に無い文書を引く質問"])
        self.assertEqual(response.context["case_total"], 2)
        self.assertContains(response, "失敗ケースのみ表示中")

    def test_絞り込み導線が既定の画面に出ている(self):
        self._golden("結合試験の完了判定は？", self.hit_document)
        self._run()
        self._regress()

        response = self.client.get(self.url)

        self.assertContains(response, "失敗ケースだけに絞り込む")
        self.assertContains(response, "cases=failed")

    def test_詳細表はケース別結果より後ろに置く(self):
        self._golden("結合試験の完了判定は？", self.hit_document)
        self._run()

        response = self.client.get(self.url)
        cases, diff_table, definitions, golden = self._order(
            response, "ケース別の結果", "前回との差分（詳細）", "指標の定義", "Golden を追加する"
        )

        self.assertLess(cases, diff_table)
        self.assertLess(diff_table, definitions)
        self.assertLess(definitions, golden)

    def test_悪化が無ければ悪化なしと明示する(self):
        self._golden("結合試験の完了判定は？", self.hit_document)
        self._run()
        self._run()

        response = self.client.get(self.url)

        self.assertContains(response, "前回より悪化した指標はありません")
