"""SEC-10: Broker監査ストアの書込み失敗時、外部操作を実行しない(fail closed)。"""

from __future__ import annotations

import hashlib
import uuid

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.pmo_authority.models import CapabilityStatus, ExecutionCapability
from apps.pmo_authority.services import audit, broker, policy_bundle
from apps.pmo_authority.services.authority import CapabilityRequest, issue_capability
from apps.projects.models import Project

NOW = timezone.now()


class AuditFailClosedTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        policy_bundle.publish_bundle(content_sha256="bundle-hash", commit_sha="test-commit")

    def _payload_hash(self) -> str:
        return hashlib.sha256(b"payload").hexdigest()

    def _capability(self):
        request = CapabilityRequest(
            tenant=self.tenant,
            project=self.project,
            work_item_id=uuid.uuid4(),
            plan_version=1,
            policy_bundle_sha256="bundle-hash",
            requested_action="slack.create_draft",
            resource_id="channel-1",
            payload_sha256=self._payload_hash(),
            evidence_bundle_sha256="evidence-hash",
            approved_by_subject_id="user-1",
        )
        return issue_capability(request, now=NOW)

    def test_SEC10_監査書込み失敗時はcapabilityの状態変更ごとロールバックされる(self) -> None:
        capability = self._capability()
        original_record_event = broker.audit.record_event

        def _boom(**kwargs):
            raise RuntimeError("監査ストアへの書込みに失敗しました（模擬障害）。")

        broker.audit.record_event = _boom
        try:
            with self.assertRaises(RuntimeError):
                broker.verify_and_execute(
                    capability,
                    connector="fake",
                    operation="create_draft",
                    current_payload_sha256=self._payload_hash(),
                    expected_tenant_id=self.tenant.id,
                    expected_project_id=self.project.id,
                    now=NOW,
                    correlation_id=uuid.uuid4(),
                )
        finally:
            broker.audit.record_event = original_record_event

        capability.refresh_from_db()
        self.assertEqual(
            capability.status,
            CapabilityStatus.ISSUED,
            "監査書込みが失敗した場合、capabilityはCONSUMEDへ進んではならない（fail closed）。",
        )

    def test_監査書込みが成功すれば通常通りCONSUMEDになる(self) -> None:
        capability = self._capability()

        receipt = broker.verify_and_execute(
            capability,
            connector="fake",
            operation="create_draft",
            current_payload_sha256=self._payload_hash(),
            expected_tenant_id=self.tenant.id,
            expected_project_id=self.project.id,
            now=NOW,
            correlation_id=uuid.uuid4(),
        )

        self.assertIn("external_id", receipt)
        capability.refresh_from_db()
        self.assertEqual(capability.status, CapabilityStatus.CONSUMED)
