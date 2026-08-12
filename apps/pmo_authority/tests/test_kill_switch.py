"""kill switch（安全施策.md SC-08: five段階の緊急停止）を検証する。"""

from __future__ import annotations

import hashlib
import uuid

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.pmo_authority.models import CapabilityStatus, KillSwitch, KillSwitchScope
from apps.pmo_authority.services import broker, kill_switch
from apps.pmo_authority.services.authority import (
    CapabilityRequest,
    KillSwitchTrippedError,
    issue_capability,
)
from apps.projects.models import Project

NOW = timezone.now()


class KillSwitchTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")


class CheckKillSwitchesTests(KillSwitchTestBase):
    def test_kill_switchが無ければ通過する(self) -> None:
        result = kill_switch.check_kill_switches(
            tenant_id=self.tenant.id, project_id=self.project.id, connector="fake", operation="create_draft"
        )

        self.assertIsNone(result)

    def test_globalが作動中なら全て拒否される(self) -> None:
        KillSwitch.objects.create(scope=KillSwitchScope.GLOBAL, reason="緊急メンテナンス")

        result = kill_switch.check_kill_switches(
            tenant_id=self.tenant.id, project_id=self.project.id, connector="fake", operation="create_draft"
        )

        self.assertIsNotNone(result)
        self.assertIn("緊急メンテナンス", result)

    def test_tenant単位の停止は他テナントに影響しない(self) -> None:
        other_tenant = Tenant.objects.create(code="other", name="OTHER")
        KillSwitch.objects.create(scope=KillSwitchScope.TENANT, tenant=self.tenant, reason="このテナントだけ停止")

        blocked = kill_switch.check_kill_switches(
            tenant_id=self.tenant.id, project_id=self.project.id, connector="fake", operation="create_draft"
        )
        allowed = kill_switch.check_kill_switches(
            tenant_id=other_tenant.id, project_id=self.project.id, connector="fake", operation="create_draft"
        )

        self.assertIsNotNone(blocked)
        self.assertIsNone(allowed)

    def test_project単位の停止(self) -> None:
        other_project = Project.objects.create(tenant=self.tenant, code="p2", name="別案件")
        KillSwitch.objects.create(scope=KillSwitchScope.PROJECT, project=self.project, reason="この案件だけ停止")

        blocked = kill_switch.check_kill_switches(
            tenant_id=self.tenant.id, project_id=self.project.id, connector="fake", operation="create_draft"
        )
        allowed = kill_switch.check_kill_switches(
            tenant_id=self.tenant.id, project_id=other_project.id, connector="fake", operation="create_draft"
        )

        self.assertIsNotNone(blocked)
        self.assertIsNone(allowed)

    def test_connector単位の停止(self) -> None:
        KillSwitch.objects.create(scope=KillSwitchScope.CONNECTOR, connector="fake", reason="fake connector停止")

        result = kill_switch.check_kill_switches(
            tenant_id=self.tenant.id, project_id=self.project.id, connector="fake", operation="create_draft"
        )

        self.assertIsNotNone(result)

    def test_operation単位の停止(self) -> None:
        KillSwitch.objects.create(
            scope=KillSwitchScope.OPERATION, operation="create_draft", reason="この操作だけ停止"
        )

        result = kill_switch.check_kill_switches(
            tenant_id=self.tenant.id, project_id=self.project.id, connector="fake", operation="create_draft"
        )

        self.assertIsNotNone(result)

    def test_解除済みのkill_switchは無視される(self) -> None:
        KillSwitch.objects.create(scope=KillSwitchScope.GLOBAL, is_tripped=False, reason="もう解除した")

        result = kill_switch.check_kill_switches(
            tenant_id=self.tenant.id, project_id=self.project.id, connector="fake", operation="create_draft"
        )

        self.assertIsNone(result)


class KillSwitchModelConstraintTests(KillSwitchTestBase):
    def test_globalスコープにtenantを紐付けると拒否される(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            KillSwitch.objects.create(scope=KillSwitchScope.GLOBAL, tenant=self.tenant)

    def test_connectorスコープでconnector未指定だと拒否される(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            KillSwitch.objects.create(scope=KillSwitchScope.CONNECTOR, connector="")


class AuthorityKillSwitchIntegrationTests(KillSwitchTestBase):
    """安全施策.md SC-08: kill switch はAuthority側でも確認する
    （セキュリティレビュー指摘対応）。"""

    def _request(self) -> CapabilityRequest:
        return CapabilityRequest(
            tenant=self.tenant,
            project=self.project,
            work_item_id=uuid.uuid4(),
            plan_version=1,
            policy_bundle_sha256="bundle-hash",
            requested_action="slack.create_draft",
            resource_id="channel-1",
            payload_sha256=hashlib.sha256(b"payload").hexdigest(),
            evidence_bundle_sha256="evidence-hash",
            approved_by_subject_id="user-1",
        )

    def test_global_kill_switch作動中はcapability発行自体を拒否する(self) -> None:
        KillSwitch.objects.create(scope=KillSwitchScope.GLOBAL, reason="安全確認中")

        with self.assertRaises(KillSwitchTrippedError):
            issue_capability(self._request(), now=NOW)

    def test_tenant_kill_switch作動中はcapability発行自体を拒否する(self) -> None:
        KillSwitch.objects.create(scope=KillSwitchScope.TENANT, tenant=self.tenant, reason="このテナントだけ停止")

        with self.assertRaises(KillSwitchTrippedError):
            issue_capability(self._request(), now=NOW)

    def test_connectorスコープのkill_switchはcapability発行を妨げない(self) -> None:
        """connector/operationはcapability発行時点でまだ決まっていないため、
        Authority側ではglobal/tenant/projectスコープだけを確認する
        （connectorスコープはBroker実行時に別途確認される）。"""

        KillSwitch.objects.create(scope=KillSwitchScope.CONNECTOR, connector="fake", reason="fake停止")

        capability = issue_capability(self._request(), now=NOW)

        self.assertEqual(capability.status, CapabilityStatus.ISSUED)


class BrokerKillSwitchIntegrationTests(KillSwitchTestBase):
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

    def test_global_kill_switch作動中はBrokerが実行前に拒否する(self) -> None:
        capability = self._capability()
        KillSwitch.objects.create(scope=KillSwitchScope.GLOBAL, reason="安全確認中")

        with self.assertRaises(broker.CapabilityRejected):
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

        capability.refresh_from_db()
        self.assertEqual(capability.status, CapabilityStatus.ISSUED)

    def test_kill_switch無しなら通常通り実行される(self) -> None:
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
