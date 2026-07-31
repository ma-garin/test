"""成果物生成のテスト。

確認したいのは文章の見た目ではなく、**本文へ書かれた数字が DB と一致すること**。
「進捗62%」と書いてあるのに DB を数え直すと違う、という壊れ方が最も危険なため、
件数はテスト側でも独立に数えて突き合わせる。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.agents.models import AgentStep
from apps.dashboard.models import Alert
from apps.pmo.models import Deliverable, PlanDraft
from apps.pmo.services import generators
from apps.pmo.services.generators import minutes
from apps.projects.models import (
    Defect,
    Issue,
    Milestone,
    Project,
    QualityMetric,
    Risk,
    Severity,
    WbsTask,
)

TODAY = date(2026, 7, 31)

NOTES = """本日の議題は結合試験の遅延について。
決定: リリース日は 8/31 のまま据え置く
・宿題: 試験環境の増設をインフラ班へ依頼する
→ 障害チケット #123 の起票
懸念: 要員が 1 名離任予定
次回は 8/7 に開催
参加者: 山田、鈴木、佐藤
"""


class GeneratorTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="gen-user",
            email="gen-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.empty_project = Project.objects.create(
            tenant=self.tenant, code="p0", name="材料なし案件"
        )
        self.client.force_login(self.user)

    def _seed(self) -> None:
        """報告書の材料。件数はテスト本体でも数え直す。"""

        WbsTask.objects.create(
            project=self.project,
            wbs_code="1-1",
            name="要件定義",
            status=WbsTask.Status.DONE,
            planned_end=TODAY - timedelta(days=30),
            progress_percent=Decimal("100"),
        )
        WbsTask.objects.create(
            project=self.project,
            wbs_code="1-2",
            name="基本設計",
            status=WbsTask.Status.IN_PROGRESS,
            planned_end=TODAY - timedelta(days=3),
            progress_percent=Decimal("60"),
            is_critical_path=True,
            next_action="レビュー指摘のクローズ",
        )
        WbsTask.objects.create(
            project=self.project,
            wbs_code="2-1",
            name="結合試験",
            status=WbsTask.Status.NOT_STARTED,
            planned_end=TODAY + timedelta(days=20),
            progress_percent=Decimal("0"),
        )
        Issue.objects.create(
            project=self.project,
            title="環境が足りない",
            status=Issue.Status.OPEN,
            severity=Severity.HIGH,
            due_date=TODAY - timedelta(days=1),
        )
        Issue.objects.create(
            project=self.project, title="解決済みの課題", status=Issue.Status.CLOSED
        )
        Risk.objects.create(
            project=self.project,
            title="要員離任",
            status=Risk.Status.MONITORING,
            probability=4,
            impact=5,
        )
        Defect.objects.create(
            project=self.project,
            title="ログイン不可",
            severity=Severity.CRITICAL,
            status=Defect.Status.NEW,
            phase="結合試験",
        )
        Defect.objects.create(
            project=self.project,
            title="表示崩れ",
            severity=Severity.LOW,
            status=Defect.Status.CLOSED,
            phase="結合試験",
        )
        Defect.objects.create(
            project=self.project,
            title="集計誤り",
            severity=Severity.HIGH,
            status=Defect.Status.FIXING,
            phase="単体試験",
        )
        QualityMetric.objects.create(
            project=self.project,
            measured_on=TODAY,
            metric_key="test_pass_rate",
            metric_label="テスト消化率",
            value=Decimal("80"),
            target_value=Decimal("95"),
            higher_is_better=True,
            unit="%",
        )
        Alert.objects.create(
            project=self.project,
            category=Alert.Category.SCHEDULE,
            severity=Alert.Severity.CRITICAL,
            title="遅延の兆候",
            detected_at=timezone.now(),
        )
        Milestone.objects.create(
            project=self.project, name="設計完了", planned_date=TODAY, is_gate=True
        )


class ReportGenerationTests(GeneratorTestBase):
    def test_weekly_report_numbers_match_db(self) -> None:
        self._seed()
        document = generators.build_document(self.project, "weekly_report", today=TODAY)

        task_total = WbsTask.objects.filter(project=self.project).count()
        overdue = (
            WbsTask.objects.filter(project=self.project, planned_end__lt=TODAY)
            .exclude(status=WbsTask.Status.DONE)
            .count()
        )

        self.assertTrue(document.has_material)
        self.assertIn(f"タスク {task_total}件", document.body)
        self.assertIn(f"期限超過 {overdue}件", document.body)
        self.assertIn("うちクリティカルパス上 1件", document.body)
        # 平均進捗 = (100 + 60 + 0) / 3
        self.assertIn("平均進捗 53.3%", document.body)
        self.assertIn("未解決 1件", document.body)
        self.assertIn("テスト消化率", document.body)
        self.assertTrue(any(item.source == "projects.WbsTask" for item in document.evidence))

    def test_週次と月次で期間中の動きが変わる(self) -> None:
        """現在値だけを並べると週次と月次が同じ本文になる。期間の節で差が出ること。"""

        self._seed()
        # 20 日前に解決した課題は、月次には入るが週次には入らない。
        Issue.objects.create(
            project=self.project,
            title="20日前に解決した課題",
            status=Issue.Status.RESOLVED,
            severity=Severity.MEDIUM,
            resolved_at=timezone.now() - timedelta(days=20),
        )

        weekly = generators.build_document(self.project, "weekly_report", today=TODAY)
        monthly = generators.build_document(self.project, "monthly_report", today=TODAY)

        self.assertIn("今期間の動き", weekly.body)
        self.assertIn("解決した課題 0件", weekly.body)
        self.assertIn("解決した課題 1件", monthly.body)

    def test_期間中に動きが無ければその旨を書く(self) -> None:
        self._seed()

        document = generators.build_document(self.project, "weekly_report", today=TODAY)

        self.assertIn("今期間の動き", document.body)

    def test_incident_summary_counts_by_severity_and_phase(self) -> None:
        self._seed()
        document = generators.build_document(self.project, "incident_summary", today=TODAY)

        total = Defect.objects.filter(project=self.project).count()
        open_count = (
            Defect.objects.filter(project=self.project)
            .exclude(status=Defect.Status.CLOSED)
            .count()
        )

        self.assertIn(f"総数 {total}件", document.body)
        self.assertIn(f"未クローズ {open_count}件", document.body)
        self.assertIn("重大: 1件", document.body)
        self.assertIn("結合試験: 2件", document.body)
        self.assertIn("ログイン不可", document.body)

    def test_empty_project_does_not_crash_and_reports_no_material(self) -> None:
        document = generators.build_document(self.empty_project, "weekly_report", today=TODAY)

        self.assertFalse(document.has_material)
        self.assertIn("生成に使える材料がありません。", document.body)
        self.assertTrue(document.warnings)

    def test_no_material_deliverable_is_blocked_from_approval(self) -> None:
        result = generators.generate_and_save(
            project=self.empty_project,
            generator_key="weekly_report",
            user=self.user,
            today=TODAY,
        )

        self.assertTrue(result.ok)
        self.assertFalse(result.deliverable.can_request_approval)


class MinutesGenerationTests(GeneratorTestBase):
    def test_extraction_classifies_and_keeps_unclassified(self) -> None:
        extraction = minutes.extract(NOTES)

        decisions = [line.text for line in extraction.of(minutes.DECISION)]
        todos = [line.text for line in extraction.of(minutes.TODO)]
        concerns = [line.text for line in extraction.of(minutes.CONCERN)]
        unclassified = [line.text for line in extraction.of(minutes.UNCLASSIFIED)]

        self.assertIn("リリース日は 8/31 のまま据え置く", decisions)
        self.assertIn("試験環境の増設をインフラ班へ依頼する", todos)
        self.assertIn("障害チケット #123 の起票", todos)
        self.assertIn("要員が 1 名離任予定", concerns)
        # 分類できない行は捨てずに残す。原文と突き合わせられなくなるため。
        self.assertIn("参加者: 山田、鈴木、佐藤", unclassified)
        self.assertEqual(extraction.total, extraction.classified + len(unclassified))

    def test_minutes_document_keeps_every_line(self) -> None:
        document = generators.build_document(
            self.project, "meeting_minutes", notes=NOTES, today=TODAY
        )

        self.assertIn("未分類（原文のまま保持）", document.body)
        self.assertIn("参加者: 山田、鈴木、佐藤", document.body)
        self.assertIn("原文 7行（空行を除く）", document.body)
        self.assertTrue(document.warnings)

    def test_action_items_document_lists_todo(self) -> None:
        document = generators.build_document(
            self.project, "action_items", notes=NOTES, today=TODAY
        )

        self.assertIn("□ 試験環境の増設をインフラ班へ依頼する", document.body)
        self.assertIn("決定事項（1件）", document.body)

    def test_empty_notes_reports_no_material(self) -> None:
        document = generators.build_document(
            self.project, "meeting_minutes", notes="   \n\n", today=TODAY
        )

        self.assertFalse(document.has_material)


class PlanDraftGenerationTests(GeneratorTestBase):
    def test_plan_draft_uses_wbs_and_records_review_points(self) -> None:
        self._seed()
        result = generators.generate_and_save(
            project=self.project, generator_key="plan_draft", user=self.user, today=TODAY
        )

        draft = PlanDraft.objects.get(project=self.project)

        self.assertTrue(result.ok)
        self.assertIn("マイルストーン案", result.document.body)
        self.assertIn("設計完了", result.document.body)
        self.assertTrue(draft.review_points)
        self.assertTrue(
            any("クリティカルパス" in point for point in draft.review_points),
            draft.review_points,
        )

    def test_plan_draft_without_wbs_reports_no_material(self) -> None:
        document = generators.build_document(self.empty_project, "plan_draft", today=TODAY)

        self.assertFalse(document.has_material)


class DeliverableEditorViewTests(GeneratorTestBase):
    def test_generate_creates_deliverable_with_evidence_steps(self) -> None:
        self._seed()
        response = self.client.post(
            reverse("pmo:deliverables"),
            {"action": "generate", "project": str(self.project.pk), "generator": "weekly_report"},
        )

        deliverable = Deliverable.objects.get(project=self.project)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(deliverable.ai_generated_body)
        # 確定本文は空のまま。人が編集していないのに赤字率 0% と見せない。
        self.assertEqual(deliverable.body, "")
        self.assertIsNotNone(deliverable.agent_run)
        self.assertTrue(AgentStep.objects.filter(run=deliverable.agent_run).exists())

    def test_generate_requires_notes_for_minutes(self) -> None:
        response = self.client.post(
            reverse("pmo:deliverables"),
            {"action": "generate", "project": str(self.project.pk), "generator": "meeting_minutes"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Deliverable.objects.exists())

    def test_saving_edited_body_changes_correction_rate(self) -> None:
        deliverable = Deliverable.objects.create(
            project=self.project,
            title="週次報告",
            kind=Deliverable.Kind.WEEKLY_REPORT,
            ai_generated_body="進捗は 60% です。\n課題は 2 件です。\n",
        )

        self.assertEqual(deliverable.correction_rate, 1.0)

        response = self.client.post(
            reverse("pmo:deliverables"),
            {
                "action": "save",
                "deliverable": str(deliverable.pk),
                "title": "週次報告（確定）",
                "body": "進捗は 60% です。\n課題は 2 件です。ただし 1 件は重大です。\n",
            },
        )
        deliverable.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(deliverable.title, "週次報告（確定）")
        self.assertLess(deliverable.correction_rate, 1.0)
        self.assertGreater(deliverable.correction_rate, 0.0)

    def test_approved_deliverable_cannot_be_edited(self) -> None:
        deliverable = Deliverable.objects.create(
            project=self.project,
            title="承認済み報告",
            kind=Deliverable.Kind.WEEKLY_REPORT,
            status=Deliverable.Status.APPROVED,
            ai_generated_body="元本文",
            body="確定本文",
        )

        self.client.post(
            reverse("pmo:deliverables"),
            {
                "action": "save",
                "deliverable": str(deliverable.pk),
                "title": "書き換え",
                "body": "書き換え本文",
            },
        )
        deliverable.refresh_from_db()

        self.assertEqual(deliverable.body, "確定本文")

    def test_editor_form_and_diff_are_rendered(self) -> None:
        deliverable = Deliverable.objects.create(
            project=self.project,
            title="週次報告",
            kind=Deliverable.Kind.WEEKLY_REPORT,
            ai_generated_body="A行\nB行\n",
            body="A行\nC行\n",
        )
        response = self.client.get(
            reverse("pmo:deliverables"), {"deliverable": str(deliverable.pk)}
        )
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="action" value="save"', content)
        self.assertIn("AI生成本文との差分", content)
        self.assertIn("成果物を生成する", content)
