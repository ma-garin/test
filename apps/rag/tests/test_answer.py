"""回答生成 第1層（根拠アセンブラ）のテスト。

ADR-0004 の唯一の約束「出所を持てない主張は書かない」を固定する。
ここが崩れると、根拠を追えるという前提が壊れ、事実誤認 0 件も担保できなくなる。
"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.agents.models import Recommendation
from apps.documents.models import Document
from apps.projects.models import Project
from apps.rag.models import Chunk, RetrievalQuery, VectorIndex
from apps.rag.services.answer import (
    GENERAL_DISCLAIMER,
    AssembledAnswer,
    Claim,
    Section,
    assemble,
    save,
)


class _Hit:
    """`retriever.search()` の戻り値の最小形。"""

    def __init__(self, chunk, score=0.5):
        self.chunk = chunk
        self.final_score = score


class _Evidence:
    def __init__(self, recommendation=Recommendation.ANSWER, missing=()):
        self.recommendation = recommendation
        self.missing_information = list(missing)


class _Intent:
    def __init__(self, viewpoints=()):
        self.viewpoints = list(viewpoints)


class SectionGuardTests(TestCase):
    """出所の無い主張を受け付けないこと。ここが第1層の要。"""

    def test_出所の無い主張は追加できない(self):
        section = Section(key="grounded", title="登録情報から確認できること")

        added = section.add(Claim(text="要員が不足している"))

        self.assertFalse(added)
        self.assertTrue(section.is_empty)

    def test_チャンク由来の主張は追加できる(self):
        section = Section(key="grounded", title="x")

        self.assertTrue(section.add(Claim(text="a", source_chunk=object())))

    def test_フィールド由来の主張は追加できる(self):
        section = Section(key="context", title="x")

        self.assertTrue(section.add(Claim(text="a", source_field="projects.Project.progress")))

    def test_一般知識の節だけは出所なしを許す(self):
        section = Section(key="general", title="x", is_general=True)

        self.assertTrue(section.add(Claim(text="一般論")))

    def test_空の節も該当なしを必ず出す(self):
        """空を省略すると「調べたが無い」と「調べていない」が区別できない。"""

        rendered = Section(key="unverified", title="資料上は確認できないこと").render()

        self.assertIn("該当なし", rendered)

    def test_一般知識の節には但し書きが付く(self):
        section = Section(key="general", title="x", is_general=True)
        section.add(Claim(text="一般論"))

        self.assertIn(GENERAL_DISCLAIMER, section.render())


class AssembleTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="ans", name="ANSWER")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件P")
        self.document = Document.objects.create(
            tenant=self.tenant, title="設計書A", file="documents/a.pdf"
        )
        self.index = VectorIndex.objects.create(tenant=self.tenant)
        self.chunk = Chunk.objects.create(
            index=self.index,
            document=self.document,
            chunk_key="a-1",
            text="結合試験は7月末までに完了する計画である。",
            position=0,
        )

    def test_検索結果が主張と引用になる(self):
        answer = assemble(
            question="結合試験の期限は？",
            hits=[_Hit(self.chunk)],
            evidence=_Evidence(),
            intent_result=_Intent(),
        )

        grounded = answer.section("grounded")
        self.assertEqual(len(grounded.claims), 1)
        self.assertIs(grounded.claims[0].source_chunk, self.chunk)
        self.assertIn("結合試験", grounded.claims[0].quote)

    def test_根拠が無いときは確認できないことへ回る(self):
        answer = assemble(
            question="要員は足りているか",
            hits=[],
            evidence=_Evidence(recommendation=Recommendation.ASK_CLARIFICATION),
            intent_result=_Intent(),
        )

        self.assertTrue(answer.section("grounded").is_empty)
        self.assertFalse(answer.section("unverified").is_empty)
        self.assertIn("要員は足りているか", answer.body())

    def test_一般知識は別の節へ隔離される(self):
        answer = assemble(
            question="q",
            hits=[_Hit(self.chunk)],
            evidence=_Evidence(),
            intent_result=_Intent(viewpoints=["体制", "スケジュール"]),
        )

        general = answer.section("general")
        grounded = answer.section("grounded")

        self.assertEqual(len(general.claims), 2)
        # 一般論が登録情報の節へ混ざらないこと。混ざると事実確認の対象が濁る。
        self.assertTrue(all(c.source_chunk is not None for c in grounded.claims))

    def test_本文に7つの見出しがすべて出る(self):
        body = assemble(
            question="q", hits=[], evidence=_Evidence(), intent_result=_Intent()
        ).body()

        for title in (
            "判断サマリ",
            "登録情報から確認できること",
            "案件データから確認できること",
            "一般的な観点",
            "資料上は確認できないこと",
        ):
            with self.subTest(title=title):
                self.assertIn(title, body)

    def test_根拠不足なら断定しない文面になる(self):
        answer = assemble(
            question="q",
            hits=[],
            evidence=_Evidence(recommendation=Recommendation.ASK_CLARIFICATION),
            intent_result=_Intent(),
        )

        self.assertIn("断定できません", answer.summary)
        self.assertEqual(answer.recommendation, Recommendation.ASK_CLARIFICATION)

    def test_案件データは出所フィールドを持つ(self):
        class _Context:
            progress_percent = 62
            open_issues = 3
            open_risks = 2
            open_defects = 1
            overdue_tasks = 4

        answer = assemble(
            question="q",
            hits=[],
            evidence=_Evidence(),
            intent_result=_Intent(),
            project_context=_Context(),
        )
        context = answer.section("context")

        self.assertEqual(len(context.claims), 5)
        self.assertTrue(all(c.source_field for c in context.claims))

    def test_節あたりの主張数に上限がある(self):
        """読み切れない量を出すと結局どれも読まれない。"""

        hits = [_Hit(self.chunk) for _ in range(20)]
        answer = assemble(
            question="q", hits=hits, evidence=_Evidence(), intent_result=_Intent()
        )

        self.assertLessEqual(len(answer.section("grounded").claims), 6)


class SaveTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="sav", name="SAVE")
        self.document = Document.objects.create(
            tenant=self.tenant, title="設計書B", file="documents/b.pdf"
        )
        self.index = VectorIndex.objects.create(tenant=self.tenant)
        self.chunk = Chunk.objects.create(
            index=self.index,
            document=self.document,
            chunk_key="b-1",
            text="移行リハーサルは8月に実施する。",
            position=0,
        )
        self.query = RetrievalQuery.objects.create(
            tenant=self.tenant, question="移行はいつ？"
        )

    def test_引用が生成と同時に埋まる(self):
        """事後の対応付けを行わないこと。推測で紐付けると根拠が追えなくなる。"""

        assembled = assemble(
            question="移行はいつ？",
            hits=[_Hit(self.chunk)],
            evidence=_Evidence(),
            intent_result=_Intent(),
        )
        answer = save(self.query, assembled)

        self.assertEqual(answer.citations.count(), 1)
        citation = answer.citations.first()
        self.assertEqual(citation.chunk, self.chunk)
        self.assertIn("移行リハーサル", citation.quoted_text)

    def test_作り直しても引用が二重にならない(self):
        assembled = assemble(
            question="q", hits=[_Hit(self.chunk)], evidence=_Evidence(), intent_result=_Intent()
        )
        save(self.query, assembled)
        answer = save(self.query, assembled)

        self.assertEqual(answer.citations.count(), 1)

    def test_一般知識は引用を作らない(self):
        assembled = assemble(
            question="q",
            hits=[],
            evidence=_Evidence(),
            intent_result=_Intent(viewpoints=["体制", "品質"]),
        )
        answer = save(self.query, assembled)

        self.assertEqual(answer.citations.count(), 0)

    def test_LLM未使用ならプロバイダは空(self):
        """第1層のみで作ったことが後から分かるようにする。"""

        assembled = assemble(
            question="q", hits=[_Hit(self.chunk)], evidence=_Evidence(), intent_result=_Intent()
        )
        answer = save(self.query, assembled)

        self.assertEqual(answer.provider, "")
        self.assertEqual(answer.model, "")

    def test_根拠の割合が記録される(self):
        assembled = assemble(
            question="q",
            hits=[_Hit(self.chunk)],
            evidence=_Evidence(),
            intent_result=_Intent(viewpoints=["体制"]),
        )
        answer = save(self.query, assembled)

        # 全2主張のうちチャンク由来1件 → 50%
        self.assertEqual(answer.knowledge_balance, 50)


class ToolRegistrationTests(TestCase):
    def test_answer_questionはLLM不要で登録されている(self):
        from apps.agents.services.tools import registry

        self.assertIn("answer_question", registry.available(llm_enabled=False))
