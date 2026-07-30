"""業務データ RAG（過去障害事例検索・案件別ナレッジ・類似案件）の回帰テスト。

外部 API は使わない（AI_PROVIDER=local_hash の既定経路のみ）。
テナント越境は情報漏洩に直結するため、必ず固定する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Tenant, User
from apps.documents.models import Document, DocumentPage, FileType
from apps.projects.models import Defect, Issue, Project, Risk
from apps.rag.models import Chunk, ChunkSourceType
from apps.rag.services import chat
from apps.rag.services import project_context as project_context_service
from apps.rag.services.business_indexer import ensure_tenant_index, index_business_data
from apps.rag.services.indexer import rebuild_index
from apps.rag.services.retriever import search
from apps.rag.services.similar_projects import similar_projects


class BusinessIndexerTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other_tenant = Tenant.objects.create(code="globex", name="GLOBEX")
        self.project = Project.objects.create(tenant=self.tenant, code="atlas", name="Atlas刷新")
        self.other_project = Project.objects.create(
            tenant=self.other_tenant, code="zeus", name="Zeus基盤"
        )
        self.defect = Defect.objects.create(
            project=self.project,
            title="決済APIがタイムアウトする",
            description="夜間バッチで決済APIが応答せず、再送処理で復旧した。",
            phase="結合試験",
        )
        Defect.objects.create(
            project=self.other_project,
            title="決済APIがタイムアウトする",
            description="他テナントの不具合。検索結果に出てはいけない。",
            phase="結合試験",
        )
        self.index = ensure_tenant_index(self.tenant)
        self.other_index = ensure_tenant_index(self.other_tenant)

    def test_不具合を検索して引ける(self):
        index_business_data(self.index)

        hits = search(self.index, "決済API タイムアウト")

        self.assertTrue(hits)
        self.assertTrue(any(hit.chunk.source_type == ChunkSourceType.DEFECT for hit in hits))
        self.assertTrue(any("不具合" in hit.chunk.source_title for hit in hits))

    def test_他テナントの業務データは検索結果に出ない(self):
        index_business_data(self.index)
        index_business_data(self.other_index)

        hits = search(self.index, "決済API タイムアウト")

        self.assertTrue(hits)

        for hit in hits:
            self.assertNotEqual(hit.chunk.project_id, self.other_project.pk)
            self.assertEqual(hit.chunk.index_id, self.index.pk)

    def test_他テナントの案件は取り込めない(self):
        with self.assertRaises(ValueError):
            index_business_data(self.index, project=self.other_project)

    def test_案件を指定するとその案件のデータだけが出る(self):
        sibling = Project.objects.create(tenant=self.tenant, code="orion", name="Orion保守")
        Issue.objects.create(project=sibling, title="決済APIの監視設定が未整備")
        index_business_data(self.index)

        hits = search(self.index, "決済API", project=self.project)

        self.assertTrue(hits)

        for hit in hits:
            self.assertEqual(hit.chunk.project_id, self.project.pk)

    def test_業務データを除外すると文書だけになる(self):
        index_business_data(self.index)

        hits = search(self.index, "決済API タイムアウト", include_business=False)

        self.assertEqual(hits, [])

    def test_差分インデックスは更新されたレコードだけ再処理する(self):
        first = index_business_data(self.index)

        self.assertEqual(first.created, 1)

        second = index_business_data(self.index)

        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 0)
        self.assertEqual(second.unchanged, 1)

        self.defect.title = "決済APIが常にタイムアウトする"
        self.defect.save()
        Issue.objects.create(project=self.project, title="監視アラートの閾値を見直す")

        third = index_business_data(self.index)

        self.assertEqual(third.updated, 1)
        self.assertEqual(third.created, 1)
        self.assertEqual(third.unchanged, 0)

    def test_元レコードが消えたらチャンクも消える(self):
        index_business_data(self.index)
        self.defect.delete()

        result = index_business_data(self.index)

        self.assertEqual(result.deleted, 1)
        self.assertFalse(
            Chunk.objects.filter(index=self.index)
            .exclude(source_type=ChunkSourceType.DOCUMENT)
            .exists()
        )

    def test_文書の再構築で業務データのチャンクが消えない(self):
        index_business_data(self.index)
        document = Document.objects.create(
            tenant=self.tenant, title="品質基準書", file_type=FileType.PDF
        )
        DocumentPage.objects.create(
            document=document, page_number=1, content="結合試験の完了判定は不具合密度で決める。"
        )

        rebuild_index(self.index)

        self.assertTrue(
            Chunk.objects.filter(index=self.index, source_type=ChunkSourceType.DEFECT).exists()
        )
        self.assertTrue(
            Chunk.objects.filter(index=self.index, source_type=ChunkSourceType.DOCUMENT).exists()
        )


class ProjectContextTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo", email="pmo@example.com", password="test-password", tenant=self.tenant
        )
        self.project = Project.objects.create(
            tenant=self.tenant, code="atlas", name="Atlas刷新", progress_percent=45
        )
        Issue.objects.create(project=self.project, title="要件の合意が取れていない")
        Risk.objects.create(project=self.project, title="要員が不足する可能性")

    def test_案件の現在値を集計する(self):
        context = project_context_service.build(self.project)

        self.assertEqual(context.open_issues, 1)
        self.assertEqual(context.open_risks, 1)
        self.assertIn("要件の合意が取れていない", context.as_text())

    def test_案件未選択なら文脈を作らない(self):
        self.assertIsNone(project_context_service.build(None))

    def test_応答の冒頭に案件の状況が付く(self):
        from apps.rag.models import ChatSession

        session = ChatSession.objects.create(
            tenant=self.tenant, user=self.user, project=self.project, title="テスト"
        )

        reply = chat.respond(session, "この案件の課題は？", None, project=self.project)

        self.assertTrue(reply.body.startswith("【いま見ている案件の状況】"))
        self.assertIn("要件の合意が取れていない", reply.body)


class SimilarProjectsTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.target = Project.objects.create(
            tenant=self.tenant, code="atlas", name="決済基盤刷新", description="決済APIの再構築"
        )
        self.similar = Project.objects.create(
            tenant=self.tenant, code="orion", name="決済基盤保守", description="決済APIの安定化"
        )
        self.unrelated = Project.objects.create(
            tenant=self.tenant, code="vega", name="人事評価制度改定", description="評価シートの刷新"
        )
        Issue.objects.create(project=self.target, title="決済APIの性能が出ない")
        Issue.objects.create(project=self.similar, title="決済APIの性能改善が必要")
        Issue.objects.create(project=self.unrelated, title="評価シートの回収が遅い")

    def test_似た案件が上位に来る(self):
        results = similar_projects(self.target, Project.objects.filter(tenant=self.tenant))

        self.assertTrue(results)
        self.assertEqual(results[0].project.pk, self.similar.pk)
        self.assertNotIn(self.target.pk, [item.project.pk for item in results])
        self.assertTrue(results[0].reason)

    def test_候補が自分だけなら空を返す(self):
        results = similar_projects(self.target, Project.objects.filter(pk=self.target.pk))

        self.assertEqual(results, [])


class ScopedSearchViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo", email="pmo@example.com", password="test-password", tenant=self.tenant
        )
        self.project = Project.objects.create(tenant=self.tenant, code="atlas", name="Atlas刷新")
        Defect.objects.create(project=self.project, title="決済APIがタイムアウトする")
        index_business_data(ensure_tenant_index(self.tenant))
        self.client.force_login(self.user)

    def test_業務データスコープで検索できる(self):
        response = self.client.get(reverse("rag:search"), {"q": "決済API", "scope": "business"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "不具合")

    def test_不正な検索範囲はテナント全体へ落ちる(self):
        response = self.client.get(reverse("rag:search"), {"q": "決済API", "scope": "invalid"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["scope"].value, "tenant")

    def test_チャット画面が表示できる(self):
        response = self.client.get(reverse("rag:chat"))

        self.assertEqual(response.status_code, 200)
