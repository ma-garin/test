"""services/outbox.py（PA-11: Authority/Broker結線）を検証する。

D-04（最初に許可する承認付き外部反映）は未決定のため、実際の外部接続は
一切行わない。ここでは fake connector 経由の配管が正しく動くことと、
承認・失効・許可connectorの再検証が機能することを検証する。
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.pmo_automation.models import (
    ApprovalRequest,
    ApprovalStatus,
    AutomationLevel,
    EvidenceBundle,
    ExecutionOutcome,
    PmoWorkItem,
    WorkItemState,
    WorkKind,
    WorkPlan,
    WorkStep,
    WorkStepState,
)
from apps.pmo_automation.services import outbox
from apps.pmo_authority.models import AuditEvent, ExecutionCapability
from apps.projects.models import Project

NOW = timezone.now()


class OutboxTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        self.approver = User.objects.create_user(
            username="approver", email="approver@example.com", password="pw", tenant=self.tenant, role=Role.PMO
        )
        self.work_item = PmoWorkItem.objects.create(
            tenant=self.tenant,
            project=self.project,
            kind=WorkKind.REPORT_CYCLE,
            source_type="alert",
            source_key="1",
            dedupe_key="outbox:1",
            state=WorkItemState.AWAITING_APPROVAL,
        )
        self.plan = WorkPlan.objects.create(
            work_item=self.work_item, version=1, automation_level=AutomationLevel.APPROVE
        )
        self.step = WorkStep.objects.create(
            plan=self.plan,
            order=1,
            kind="external_notify",
            automation_level=AutomationLevel.APPROVE,
            idempotency_key="outbox:1:1",
        )
        self.evidence = EvidenceBundle.objects.create(
            work_item=self.work_item,
            source_type="fact_check",
            source_ref="ev-1",
            scope={"tenant": self.tenant.code},
            content_hash="hash-1",
            captured_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
        self.approval = ApprovalRequest.objects.create(
            work_item=self.work_item,
            plan_version=1,
            requested_action="slack.create_draft",
            status=ApprovalStatus.APPROVED,
            required_role=Role.PMO,
        )

    def _dispatch(self, **overrides):
        defaults = {
            "step": self.step,
            "approval": self.approval,
            "connector": "fake",
            "operation": "create_draft",
            "evidence_bundles": [self.evidence],
            "expected_evidence_hash": "hash-1",
            "actual_evidence_hash": "hash-1",
            "actor_id": self.approver.id,
            "actor_subject_id": str(self.approver.id),
            "actor_role": Role.PMO,
            "required_role": Role.PMO,
            "now": NOW,
            "correlation_id": uuid.uuid4(),
        }
        defaults.update(overrides)

        return outbox.dispatch_approved_step(**defaults)


class DispatchApprovedStepTests(OutboxTestBase):
    def test_正常系はSUCCEEDEDになり受領証跡が残る(self) -> None:
        attempt = self._dispatch()

        self.assertEqual(attempt.outcome, ExecutionOutcome.SUCCEEDED)
        self.assertIn("external_id", attempt.external_receipt)
        self.step.refresh_from_db()
        self.assertEqual(self.step.state, WorkStepState.SUCCEEDED)
        self.assertEqual(ExecutionCapability.objects.count(), 1)
        self.assertTrue(AuditEvent.objects.filter(event_type="capability_consumed").exists())

    def test_冪等性_成功済みStepへの再送は二重送信しない(self) -> None:
        """セキュリティレビュー指摘対応: リトライで同じStepを二度送信しない。"""

        first = self._dispatch()

        second = self._dispatch()

        self.assertEqual(second.pk, first.pk)
        # capabilityは1回目の1件だけ。2回目はBroker/Authorityを呼んでいない。
        self.assertEqual(ExecutionCapability.objects.count(), 1)
        self.assertEqual(self.step.attempts.filter(outcome=ExecutionOutcome.SUCCEEDED).count(), 1)

    def test_許可リスト外のconnectorは拒否される(self) -> None:
        """SEC-06自体はP0スコープ外（LLM統合が無く「LLMがschema外action
        を返す」という原シナリオが発生しない）。このテストはSEC-06への
        対応ではなく、PA-11で既に導入済みのALLOWED_CONNECTORSが
        "未許可の行き先を実行させない"という近い安全性質を偶然満たして
        いることの回帰確認であり、SEC-06向けに新規実装したコードは無い。"""

        with self.assertRaises(outbox.DispatchRejected):
            self._dispatch(connector="slack")

    def test_拒否後もStepはRUNNINGのまま残らずholdに戻る(self) -> None:
        """セキュリティレビュー指摘対応: RUNNINGへ遷移させた後に検証失敗した場合、
        RUNNINGのまま放置すると以後の全リクエストが誤って「送信処理中」拒否され
        続けるため、hold へ戻すこと（デッドロック相当の不具合の回避）を確認する。"""

        with self.assertRaises(outbox.DispatchRejected):
            self._dispatch(connector="slack")

        self.step.refresh_from_db()
        self.assertEqual(self.step.state, WorkStepState.HOLD)

    def test_capability発行で予期しない例外が起きてもRUNNINGのまま残らない(self) -> None:
        """セキュリティレビュー指摘対応(2回目): issue_capability呼出しが
        try/exceptで保護されておらず、そこで例外が起きるとStepがRUNNINGの
        まま永久に残るバグがあった。"""

        original_issue_capability = outbox.authority.issue_capability

        def _boom(request, *, now, **kwargs):
            raise RuntimeError("想定外のバリデーションエラー")

        outbox.authority.issue_capability = _boom
        try:
            with self.assertRaises(RuntimeError):
                self._dispatch()
        finally:
            outbox.authority.issue_capability = original_issue_capability

        self.step.refresh_from_db()
        self.assertEqual(self.step.state, WorkStepState.HOLD)

    def test_RUNNING状態のStepへの同時dispatchは重複防止として拒否される(self) -> None:
        """select_for_update + RUNNING即時コミットによる直列化(セキュリティレビュー
        指摘対応)。ここでは実際の並行スレッドではなく、既にRUNNING状態にある
        Stepへdispatchした場合の拒否だけを確認する（TestCase内では真の並行実行は
        検証できないため、直列化の結果として現れる状態のみを見る）。"""

        self.step.state = WorkStepState.RUNNING
        self.step.save(update_fields=["state"])

        with self.assertRaises(outbox.DispatchRejected):
            self._dispatch()

    def test_承認済みでないapprovalは拒否される(self) -> None:
        self.approval.status = ApprovalStatus.PENDING
        self.approval.save(update_fields=["status"])

        with self.assertRaises(outbox.DispatchRejected):
            self._dispatch()

    def test_plan版が変わっていると承認失効として拒否される(self) -> None:
        new_plan = WorkPlan.objects.create(
            work_item=self.work_item, version=2, automation_level=AutomationLevel.APPROVE
        )
        new_step = WorkStep.objects.create(
            plan=new_plan,
            order=1,
            kind="external_notify",
            automation_level=AutomationLevel.APPROVE,
            idempotency_key="outbox:1:2",
        )
        # approval.plan_version は古い版(1)のまま。新しいplan(2)に紐づくstepへ
        # 流用しようとすると、guard_awaiting_approval_to_executingが
        # plan版不一致として拒否する。

        with self.assertRaises(outbox.DispatchRejected):
            self._dispatch(step=new_step)

    def test_作成者の自己承認は拒否される(self) -> None:
        self.approval.created_by = self.approver
        self.approval.save(update_fields=["created_by"])

        with self.assertRaises(outbox.DispatchRejected):
            self._dispatch(actor_id=self.approver.id)

    def test_根拠hashが承認時から変わっていると拒否される(self) -> None:
        with self.assertRaises(outbox.DispatchRejected):
            self._dispatch(expected_evidence_hash="hash-1", actual_evidence_hash="hash-2-changed")

    def test_Brokerに拒否されるとStepはholdでExecutionAttemptはFAILEDになる(self) -> None:
        """guardは通過するがBroker側の検証(policy bundle不一致)で拒否される経路。"""

        original_issue_capability = outbox.authority.issue_capability

        def _issue_with_stale_policy_bundle(request, *, now, **kwargs):
            capability = original_issue_capability(request, now=now, **kwargs)
            capability.policy_bundle_sha256 = "stale-bundle-from-before-capability-issued"
            capability.save(update_fields=["policy_bundle_sha256"])
            return capability

        outbox.authority.issue_capability = _issue_with_stale_policy_bundle
        try:
            attempt = self._dispatch()
        finally:
            outbox.authority.issue_capability = original_issue_capability

        # policy_bundle不一致まではoutbox.pyが渡していないため実際には成功するが、
        # signatureがpolicy_bundle_sha256の書き換え後の値と食い違うため
        # Brokerの署名検証で拒否される。
        self.assertEqual(attempt.outcome, ExecutionOutcome.FAILED)
        self.step.refresh_from_db()
        self.assertEqual(self.step.state, WorkStepState.HOLD)
