"""Authority(capability発行)・Broker(検証+実行)・Audit(hash chain)のテスト。

安全施策.md SC-05/SC-06/SC-07の開発用fake実装を検証する。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.pmo_authority.models import AuditEvent, CapabilityStatus, ExecutionCapability
from apps.pmo_authority.services import audit, broker
from apps.pmo_authority.services.authority import (
    MAX_TTL_SECONDS,
    CapabilityRequest,
    InsecureSigningKeyError,
    InvalidTtlError,
    issue_capability,
)
from apps.projects.models import Project

NOW = timezone.now()


class AuthorityBrokerTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

    def _request(self, **kwargs) -> CapabilityRequest:
        defaults = {
            "tenant": self.tenant,
            "project": self.project,
            "work_item_id": uuid.uuid4(),
            "plan_version": 1,
            "policy_bundle_sha256": "bundle-hash",
            "requested_action": "slack.create_draft",
            "resource_id": "channel-123",
            "payload_sha256": hashlib.sha256(b"payload-v1").hexdigest(),
            "evidence_bundle_sha256": "evidence-hash",
            "approved_by_subject_id": "user-1",
            "required_role": "project_owner",
        }
        defaults.update(kwargs)

        return CapabilityRequest(**defaults)


class IssueCapabilityTests(AuthorityBrokerTestBase):
    def test_capabilityが正しく発行され署名が付与される(self) -> None:
        capability = issue_capability(self._request(), now=NOW)

        self.assertEqual(capability.status, CapabilityStatus.ISSUED)
        self.assertTrue(capability.signature)
        self.assertEqual(capability.expires_at, NOW + timedelta(seconds=600))

    def test_ttlが10分を超えると拒否される(self) -> None:
        with self.assertRaises(InvalidTtlError):
            issue_capability(self._request(), now=NOW, ttl_seconds=MAX_TTL_SECONDS + 1)

    def test_ttlが0以下だと拒否される(self) -> None:
        with self.assertRaises(InvalidTtlError):
            issue_capability(self._request(), now=NOW, ttl_seconds=0)

    def test_nonceは毎回一意になる(self) -> None:
        first = issue_capability(self._request(), now=NOW)
        second = issue_capability(self._request(), now=NOW)

        self.assertNotEqual(first.nonce, second.nonce)

    @override_settings(DEBUG=False, PMO_AUTHORITY_SIGNING_KEY="")
    def test_DEBUG_Falseで署名鍵未設定なら発行を拒否する(self) -> None:
        """セキュリティレビュー指摘対応: 本番相当で開発用デフォルト鍵のまま
        気づかず運用されることを防ぐ。"""

        with self.assertRaises(InsecureSigningKeyError):
            issue_capability(self._request(), now=NOW)

    @override_settings(DEBUG=False, PMO_AUTHORITY_SIGNING_KEY="explicit-non-default-key")
    def test_DEBUG_Falseでも明示的な鍵が設定されていれば発行できる(self) -> None:
        capability = issue_capability(self._request(), now=NOW)

        self.assertTrue(capability.signature)


class ExecutionCapabilityModelConstraintTests(AuthorityBrokerTestBase):
    def test_TTL上限を直接createで迂回することはできない(self) -> None:
        """セキュリティレビュー指摘対応: issue_capability()の入口チェックだけだと
        ExecutionCapability.objects.create()を直接呼べば10分上限を迂回できてしまう
        ため、モデル層のCHECK制約でも強制する。"""

        with self.assertRaises(IntegrityError), transaction.atomic():
            ExecutionCapability.objects.create(
                tenant=self.tenant,
                project=self.project,
                work_item_id=uuid.uuid4(),
                plan_version=1,
                policy_bundle_sha256="bundle-hash",
                requested_action="slack.create_draft",
                resource_id="channel-123",
                payload_sha256="payload-hash",
                evidence_bundle_sha256="evidence-hash",
                approved_by_subject_id="user-1",
                issued_at=NOW,
                expires_at=NOW + timedelta(seconds=MAX_TTL_SECONDS + 1),
                nonce=uuid.uuid4().hex,
                idempotency_key="key-1",
                signature="sig",
            )


class VerifyAndExecuteTests(AuthorityBrokerTestBase):
    def _payload_hash(self, text: str = "payload-v1") -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def test_正当なcapabilityでfake_connectorが実行される(self) -> None:
        capability = issue_capability(
            self._request(payload_sha256=self._payload_hash()), now=NOW
        )

        receipt = broker.verify_and_execute(
            capability,
            connector="slack",
            operation="create_draft",
            current_payload_sha256=self._payload_hash(),
            now=NOW + timedelta(seconds=1),
            correlation_id=uuid.uuid4(),
        )

        self.assertIn("external_id", receipt)
        capability.refresh_from_db()
        self.assertEqual(capability.status, CapabilityStatus.CONSUMED)

    def test_署名が改ざんされたcapabilityは拒否される(self) -> None:
        capability = issue_capability(
            self._request(payload_sha256=self._payload_hash()), now=NOW
        )
        capability.signature = "tampered"
        capability.save(update_fields=["signature"])

        with self.assertRaises(broker.CapabilityRejected):
            broker.verify_and_execute(
                capability,
                connector="slack",
                operation="create_draft",
                current_payload_sha256=self._payload_hash(),
                now=NOW,
                correlation_id=uuid.uuid4(),
            )

    def test_期限切れのcapabilityは拒否される(self) -> None:
        capability = issue_capability(
            self._request(payload_sha256=self._payload_hash()), now=NOW, ttl_seconds=60
        )

        with self.assertRaises(broker.CapabilityRejected):
            broker.verify_and_execute(
                capability,
                connector="slack",
                operation="create_draft",
                current_payload_sha256=self._payload_hash(),
                now=NOW + timedelta(seconds=61),
                correlation_id=uuid.uuid4(),
            )

    def test_承認後に内容が変わっていると拒否される(self) -> None:
        capability = issue_capability(
            self._request(payload_sha256=self._payload_hash("payload-v1")), now=NOW
        )

        with self.assertRaises(broker.CapabilityRejected):
            broker.verify_and_execute(
                capability,
                connector="slack",
                operation="create_draft",
                current_payload_sha256=self._payload_hash("payload-v2-changed"),
                now=NOW,
                correlation_id=uuid.uuid4(),
            )

    def test_policy_bundleが差し替わっていると拒否される(self) -> None:
        """安全施策.md SC-06: policy bundleを差し替えると古いcapabilityは拒否される。"""

        capability = issue_capability(
            self._request(payload_sha256=self._payload_hash(), policy_bundle_sha256="bundle-v1"), now=NOW
        )

        with self.assertRaises(broker.CapabilityRejected):
            broker.verify_and_execute(
                capability,
                connector="slack",
                operation="create_draft",
                current_payload_sha256=self._payload_hash(),
                now=NOW,
                correlation_id=uuid.uuid4(),
                current_policy_bundle_sha256="bundle-v2-differs",
            )

    def test_policy_bundleが一致していれば実行できる(self) -> None:
        capability = issue_capability(
            self._request(payload_sha256=self._payload_hash(), policy_bundle_sha256="bundle-v1"), now=NOW
        )

        receipt = broker.verify_and_execute(
            capability,
            connector="slack",
            operation="create_draft",
            current_payload_sha256=self._payload_hash(),
            now=NOW,
            correlation_id=uuid.uuid4(),
            current_policy_bundle_sha256="bundle-v1",
        )

        self.assertIn("external_id", receipt)

    def test_同一capabilityの再実行はnonce再利用として拒否される(self) -> None:
        capability = issue_capability(
            self._request(payload_sha256=self._payload_hash()), now=NOW
        )
        correlation_id = uuid.uuid4()

        broker.verify_and_execute(
            capability,
            connector="slack",
            operation="create_draft",
            current_payload_sha256=self._payload_hash(),
            now=NOW,
            correlation_id=correlation_id,
        )

        with self.assertRaises(broker.CapabilityRejected):
            broker.verify_and_execute(
                capability,
                connector="slack",
                operation="create_draft",
                current_payload_sha256=self._payload_hash(),
                now=NOW + timedelta(seconds=1),
                correlation_id=correlation_id,
            )

    def test_拒否されたcapabilityはfake_connectorを呼ばずに監査記録だけ残す(self) -> None:
        capability = issue_capability(
            self._request(payload_sha256=self._payload_hash()), now=NOW
        )
        capability.signature = "tampered"
        capability.save(update_fields=["signature"])
        correlation_id = uuid.uuid4()

        with self.assertRaises(broker.CapabilityRejected):
            broker.verify_and_execute(
                capability,
                connector="slack",
                operation="create_draft",
                current_payload_sha256=self._payload_hash(),
                now=NOW,
                correlation_id=correlation_id,
            )

        capability.refresh_from_db()
        self.assertEqual(capability.status, CapabilityStatus.ISSUED)
        self.assertEqual(
            AuditEvent.objects.filter(correlation_id=correlation_id, event_type="capability_rejected").count(),
            1,
        )


class AuditHashChainTests(AuthorityBrokerTestBase):
    def test_連続するイベントのhash_chainが繋がる(self) -> None:
        capability = issue_capability(
            self._request(payload_sha256=hashlib.sha256(b"p").hexdigest()), now=NOW
        )
        correlation_id = uuid.uuid4()

        broker.verify_and_execute(
            capability,
            connector="slack",
            operation="create_draft",
            current_payload_sha256=hashlib.sha256(b"p").hexdigest(),
            now=NOW,
            correlation_id=correlation_id,
        )

        second_capability = issue_capability(
            self._request(payload_sha256=hashlib.sha256(b"p2").hexdigest()), now=NOW
        )
        broker.verify_and_execute(
            second_capability,
            connector="slack",
            operation="create_draft",
            current_payload_sha256=hashlib.sha256(b"p2").hexdigest(),
            now=NOW + timedelta(seconds=1),
            correlation_id=correlation_id,
        )

        events = list(AuditEvent.objects.filter(correlation_id=correlation_id).order_by("created_at", "id"))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].previous_hash, events[0].event_hash)
        self.assertNotEqual(events[0].event_hash, events[1].event_hash)

        # セキュリティレビュー指摘対応: 記録するだけでなく検証できること。
        self.assertEqual(audit.verify_chain(correlation_id), 2)

    def test_previous_hashが改ざんされるとverify_chainが検知する(self) -> None:
        capability = issue_capability(
            self._request(payload_sha256=hashlib.sha256(b"p").hexdigest()), now=NOW
        )
        correlation_id = uuid.uuid4()
        broker.verify_and_execute(
            capability,
            connector="slack",
            operation="create_draft",
            current_payload_sha256=hashlib.sha256(b"p").hexdigest(),
            now=NOW,
            correlation_id=correlation_id,
        )
        second_capability = issue_capability(
            self._request(payload_sha256=hashlib.sha256(b"p2").hexdigest()), now=NOW
        )
        broker.verify_and_execute(
            second_capability,
            connector="slack",
            operation="create_draft",
            current_payload_sha256=hashlib.sha256(b"p2").hexdigest(),
            now=NOW + timedelta(seconds=1),
            correlation_id=correlation_id,
        )

        second_event = AuditEvent.objects.filter(correlation_id=correlation_id).order_by("created_at", "id")[1]
        second_event.previous_hash = "tampered-hash"
        second_event.save(update_fields=["previous_hash"])

        with self.assertRaises(audit.ChainIntegrityError):
            audit.verify_chain(correlation_id)

    def test_event_hashが改ざんされるとverify_chainが検知する(self) -> None:
        capability = issue_capability(
            self._request(payload_sha256=hashlib.sha256(b"p").hexdigest()), now=NOW
        )
        correlation_id = uuid.uuid4()
        broker.verify_and_execute(
            capability,
            connector="slack",
            operation="create_draft",
            current_payload_sha256=hashlib.sha256(b"p").hexdigest(),
            now=NOW,
            correlation_id=correlation_id,
        )

        event = AuditEvent.objects.filter(correlation_id=correlation_id).first()
        event.detail = {**event.detail, "tampered": True}
        event.save(update_fields=["detail"])

        with self.assertRaises(audit.ChainIntegrityError):
            audit.verify_chain(correlation_id)

    def test_イベントが無い相関IDはverify_chainが0件で成功する(self) -> None:
        self.assertEqual(audit.verify_chain(uuid.uuid4()), 0)
