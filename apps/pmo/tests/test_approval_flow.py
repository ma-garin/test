"""承認フロー（HITL）が「人が確かめてから確定する」を満たすことのテスト。

承認履歴が残っていても、申請した本人がそのまま承認できたり、確定本文が空の
まま承認できたり、承認待ちのあいだに本文を差し替えられるなら、確認を経たとは
言えない。ここではその抜け道が塞がっていることを、状態と履歴の両方で確かめる。
"""

from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.agents.models import AgentRun, EvidenceEvaluation, Recommendation
from apps.pmo import selectors
from apps.pmo.models import Approval, Deliverable
from apps.pmo.services import approval as approval_service
from apps.projects.models import Project

BODY = "今週は結合試験を実施しました。"


class ApprovalFlowTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.author = self._user("author")
        self.approver = self._user("approver")

    def _user(self, name: str) -> User:
        return User.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )

    def _deliverable(self, **kwargs) -> Deliverable:
        defaults = {
            "project": self.project,
            "title": "週次報告",
            "kind": Deliverable.Kind.WEEKLY_REPORT,
            "ai_generated_body": BODY,
            "body": BODY,
            "status": Deliverable.Status.PENDING_APPROVAL,
            "created_by": self.author,
        }

        return Deliverable.objects.create(**{**defaults, **kwargs})

    def _status_of(self, deliverable: Deliverable) -> str:
        deliverable.refresh_from_db()

        return deliverable.status


class SeparateApproverTests(ApprovalFlowTestBase):
    """四眼原則。申請者・作成者と承認者は別人でなければならない。"""

    def test_作成者は自分の成果物を承認できない(self):
        deliverable = self._deliverable()

        result = approval_service.decide(
            deliverable=deliverable, actor=self.author, decision=Approval.Decision.APPROVED
        )

        self.assertFalse(result.ok)
        self.assertIn("四眼原則", result.message)
        self.assertEqual(self._status_of(deliverable), Deliverable.Status.PENDING_APPROVAL)
        self.assertEqual(Approval.objects.count(), 0)

    def test_承認依頼をした本人は承認できない(self):
        # 作成者が分からない成果物でも、依頼を押した人は承認できない。
        deliverable = self._deliverable(created_by=None, status=Deliverable.Status.DRAFT)
        approval_service.decide(
            deliverable=deliverable, actor=self.approver, decision=Approval.Decision.REQUESTED
        )
        deliverable.refresh_from_db()

        result = approval_service.decide(
            deliverable=deliverable, actor=self.approver, decision=Approval.Decision.APPROVED
        )

        self.assertFalse(result.ok)
        self.assertEqual(self._status_of(deliverable), Deliverable.Status.PENDING_APPROVAL)
        self.assertFalse(
            Approval.objects.filter(decision=Approval.Decision.APPROVED).exists()
        )

    def test_別人なら承認できる(self):
        deliverable = self._deliverable()

        result = approval_service.decide(
            deliverable=deliverable, actor=self.approver, decision=Approval.Decision.APPROVED
        )

        self.assertTrue(result.ok)
        self.assertEqual(self._status_of(deliverable), Deliverable.Status.APPROVED)
        self.assertEqual(
            Approval.objects.filter(actor=self.approver, decision="approved").count(), 1
        )

    @override_settings(APPROVAL_REQUIRE_SEPARATE_APPROVER=False)
    def test_承認者が1人の運用では設定で緩められる(self):
        deliverable = self._deliverable()

        result = approval_service.decide(
            deliverable=deliverable, actor=self.author, decision=Approval.Decision.APPROVED
        )

        self.assertTrue(result.ok)
        self.assertEqual(self._status_of(deliverable), Deliverable.Status.APPROVED)

    def test_作成者でも差し戻しはできる(self):
        # 締めるのは「確定」だけ。自分の成果物を取り下げる方向は止めない。
        deliverable = self._deliverable()

        result = approval_service.decide(
            deliverable=deliverable, actor=self.author, decision=Approval.Decision.REJECTED
        )

        self.assertTrue(result.ok)
        self.assertEqual(self._status_of(deliverable), Deliverable.Status.REJECTED)

    def test_画面から自己承認をPOSTしても通らない(self):
        deliverable = self._deliverable()
        self.client.force_login(self.author)

        self.client.post(
            reverse("pmo:approvals"),
            {"deliverable": str(deliverable.pk), "decision": "approved"},
        )

        self.assertEqual(self._status_of(deliverable), Deliverable.Status.PENDING_APPROVAL)
        self.assertEqual(Approval.objects.count(), 0)


class RejectedDeliverableTests(ApprovalFlowTestBase):
    """差し戻したものが行き止まりにならないこと。"""

    def test_差し戻した成果物が承認待ち一覧に戻る(self):
        deliverable = self._deliverable(status=Deliverable.Status.REJECTED)

        awaiting = selectors.deliverables_awaiting_decision_for(self.approver, self.tenant)

        self.assertIn(deliverable, list(awaiting))

    def test_承認済みは判断待ち一覧に出ない(self):
        approved = self._deliverable(status=Deliverable.Status.APPROVED)

        awaiting = selectors.deliverables_awaiting_decision_for(self.approver, self.tenant)

        self.assertNotIn(approved, list(awaiting))

    def test_差し戻し後に再申請できる(self):
        deliverable = self._deliverable(status=Deliverable.Status.REJECTED)

        result = approval_service.decide(
            deliverable=deliverable, actor=self.author, decision=Approval.Decision.REQUESTED
        )

        self.assertTrue(result.ok)
        self.assertEqual(self._status_of(deliverable), Deliverable.Status.PENDING_APPROVAL)

    def test_差し戻した成果物が承認画面に表示される(self):
        deliverable = self._deliverable(status=Deliverable.Status.REJECTED)
        self.client.force_login(self.approver)

        response = self.client.get(reverse("pmo:approvals"))

        self.assertContains(response, str(deliverable.title))
        self.assertContains(response, "承認依頼")


class ConfirmedBodyTests(ApprovalFlowTestBase):
    """人が確定本文を書いていない生成物を確定させないこと。"""

    def test_確定本文が空なら承認できない(self):
        deliverable = self._deliverable(body="")

        result = approval_service.decide(
            deliverable=deliverable, actor=self.approver, decision=Approval.Decision.APPROVED
        )

        self.assertFalse(result.ok)
        self.assertIn("確定本文が空です", result.message)
        self.assertEqual(self._status_of(deliverable), Deliverable.Status.PENDING_APPROVAL)
        self.assertEqual(Approval.objects.count(), 0)

    def test_空白だけの確定本文も承認できない(self):
        deliverable = self._deliverable(body="   \n\n")

        self.assertIn(
            "確定本文が空です",
            approval_service.blocking_reason(deliverable, actor=self.approver),
        )

    def test_確定本文が空でも承認依頼はできる(self):
        # 依頼まで止めると、直す前に行き止まりになる。締めるのは確定の側だけ。
        deliverable = self._deliverable(body="", status=Deliverable.Status.DRAFT)

        result = approval_service.decide(
            deliverable=deliverable, actor=self.author, decision=Approval.Decision.REQUESTED
        )

        self.assertTrue(result.ok)
        self.assertEqual(self._status_of(deliverable), Deliverable.Status.PENDING_APPROVAL)


class PendingApprovalEditTests(ApprovalFlowTestBase):
    """承認待ちのあいだに本文を差し替えられないこと（承認直前スワップ）。"""

    def test_承認待ちの本文を編集すると下書きへ戻り版が上がる(self):
        deliverable = self._deliverable()
        self.client.force_login(self.author)

        response = self.client.post(
            reverse("pmo:deliverables"),
            {
                "action": "save",
                "deliverable": str(deliverable.pk),
                "title": "週次報告",
                "body": "承認直前に差し替えた本文",
            },
        )
        deliverable.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(deliverable.body, "承認直前に差し替えた本文")
        self.assertEqual(deliverable.status, Deliverable.Status.DRAFT)
        self.assertEqual(deliverable.version, 2)

    def test_取り下げが履歴に残る(self):
        deliverable = self._deliverable()
        self.client.force_login(self.author)

        self.client.post(
            reverse("pmo:deliverables"),
            {
                "action": "save",
                "deliverable": str(deliverable.pk),
                "title": "週次報告",
                "body": "承認直前に差し替えた本文",
            },
        )

        withdrawn = Approval.objects.filter(
            deliverable=deliverable, decision=Approval.Decision.WITHDRAWN
        )

        self.assertEqual(withdrawn.count(), 1)
        self.assertEqual(withdrawn.first().actor, self.author)

    def test_編集後は承認できず再依頼が必要になる(self):
        deliverable = self._deliverable()
        self.client.force_login(self.author)

        self.client.post(
            reverse("pmo:deliverables"),
            {
                "action": "save",
                "deliverable": str(deliverable.pk),
                "title": "週次報告",
                "body": "承認直前に差し替えた本文",
            },
        )
        deliverable.refresh_from_db()

        result = approval_service.decide(
            deliverable=deliverable, actor=self.approver, decision=Approval.Decision.APPROVED
        )

        self.assertFalse(result.ok)
        self.assertEqual(self._status_of(deliverable), Deliverable.Status.DRAFT)

    def test_下書きの編集では版も状態も変わらない(self):
        deliverable = self._deliverable(status=Deliverable.Status.DRAFT)
        self.client.force_login(self.author)

        self.client.post(
            reverse("pmo:deliverables"),
            {
                "action": "save",
                "deliverable": str(deliverable.pk),
                "title": "週次報告",
                "body": "下書きを直した本文",
            },
        )
        deliverable.refresh_from_db()

        self.assertEqual(deliverable.status, Deliverable.Status.DRAFT)
        self.assertEqual(deliverable.version, 1)


class MissingEvidenceTests(ApprovalFlowTestBase):
    """根拠評価が無い生成物を承認へ進ませないこと。"""

    def _run(self) -> AgentRun:
        return AgentRun.objects.create(
            tenant=self.tenant,
            project=self.project,
            area=AgentRun.Area.DELIVERABLE,
            user_input="週次報告を作成して",
        )

    def test_根拠評価が無い生成物は承認申請できない(self):
        deliverable = self._deliverable(agent_run=self._run())

        self.assertFalse(deliverable.can_request_approval)

    def test_理由に根拠評価が未実施であることが出る(self):
        deliverable = self._deliverable(agent_run=self._run())

        self.assertIn(
            "根拠評価が未実施です", approval_service.blocking_reason(deliverable)
        )

    def test_根拠評価が無ければ承認も申請も通らない(self):
        deliverable = self._deliverable(agent_run=self._run())

        approve = approval_service.decide(
            deliverable=deliverable, actor=self.approver, decision=Approval.Decision.APPROVED
        )

        self.assertFalse(approve.ok)
        self.assertEqual(self._status_of(deliverable), Deliverable.Status.PENDING_APPROVAL)

        deliverable.status = Deliverable.Status.DRAFT
        deliverable.save(update_fields=["status"])
        request = approval_service.decide(
            deliverable=deliverable, actor=self.author, decision=Approval.Decision.REQUESTED
        )

        self.assertFalse(request.ok)
        self.assertEqual(self._status_of(deliverable), Deliverable.Status.DRAFT)

    def test_根拠評価があれば従来どおり承認できる(self):
        run = self._run()
        EvidenceEvaluation.objects.create(
            run=run, confidence=0.9, recommendation=Recommendation.ANSWER
        )
        deliverable = self._deliverable(agent_run=run)

        result = approval_service.decide(
            deliverable=deliverable, actor=self.approver, decision=Approval.Decision.APPROVED
        )

        self.assertTrue(result.ok)

    def test_AI不使用の成果物は根拠評価が無くても承認できる(self):
        # AI 未設定でも業務が止まらないこと。締めるのは AI 生成物だけ。
        deliverable = self._deliverable()

        result = approval_service.decide(
            deliverable=deliverable, actor=self.approver, decision=Approval.Decision.APPROVED
        )

        self.assertTrue(result.ok)
