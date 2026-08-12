"""SEC-01: 未署名または別コミットのpolicy bundleを読み込むとAuthorityが拒否する。"""

from __future__ import annotations

import hashlib
import uuid

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.pmo_authority.models import CapabilityStatus, PolicyBundle
from apps.pmo_authority.services import policy_bundle
from apps.pmo_authority.services.authority import CapabilityRequest, issue_capability
from apps.pmo_authority.services.policy_bundle import InsecureDevBundleError, PolicyBundleRejected
from apps.projects.models import Project

NOW = timezone.now()


class PublishAndVerifyBundleTests(TestCase):
    def test_publishしたbundleはverifyを通る(self) -> None:
        bundle = policy_bundle.publish_bundle(content_sha256="hash-1", commit_sha="commit-1")

        verified = policy_bundle.verify_bundle("hash-1")

        self.assertEqual(verified.pk, bundle.pk)

    def test_未登録のbundleは拒否される(self) -> None:
        with self.assertRaises(PolicyBundleRejected):
            policy_bundle.verify_bundle("never-published")

    def test_無効化済みのbundleは拒否される(self) -> None:
        bundle = policy_bundle.publish_bundle(content_sha256="hash-2", commit_sha="commit-2")
        bundle.is_active = False
        bundle.save(update_fields=["is_active"])

        with self.assertRaises(PolicyBundleRejected):
            policy_bundle.verify_bundle("hash-2")

    def test_署名が改ざんされたbundleは拒否される(self) -> None:
        bundle = policy_bundle.publish_bundle(content_sha256="hash-3", commit_sha="commit-3")
        bundle.signature = "tampered"
        bundle.save(update_fields=["signature"])

        with self.assertRaises(PolicyBundleRejected):
            policy_bundle.verify_bundle("hash-3")

    def test_同じcontent_sha256は二重発行できない(self) -> None:
        policy_bundle.publish_bundle(content_sha256="hash-4", commit_sha="commit-4")

        with self.assertRaises(Exception):
            policy_bundle.publish_bundle(content_sha256="hash-4", commit_sha="commit-4-again")


class IssueCapabilityPolicyBundleTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

    def _request(self, **kwargs) -> CapabilityRequest:
        defaults = {
            "tenant": self.tenant,
            "project": self.project,
            "work_item_id": uuid.uuid4(),
            "plan_version": 1,
            "policy_bundle_sha256": "unregistered-bundle-hash",
            "requested_action": "slack.create_draft",
            "resource_id": "channel-1",
            "payload_sha256": hashlib.sha256(b"payload").hexdigest(),
            "evidence_bundle_sha256": "evidence-hash",
            "approved_by_subject_id": "user-1",
        }
        defaults.update(kwargs)

        return CapabilityRequest(**defaults)

    def test_SEC01_未署名のpolicy_bundleを指すcapability発行は拒否される(self) -> None:
        with self.assertRaises(PolicyBundleRejected):
            issue_capability(self._request(policy_bundle_sha256="never-published"), now=NOW)

    def test_署名済みbundleを指定すれば発行できる(self) -> None:
        policy_bundle.publish_bundle(content_sha256="registered-hash", commit_sha="commit-x")

        capability = issue_capability(self._request(policy_bundle_sha256="registered-hash"), now=NOW)

        self.assertEqual(capability.status, CapabilityStatus.ISSUED)

    def test_SEC01_無効化済みbundleを指すcapability発行は拒否される(self) -> None:
        bundle = policy_bundle.publish_bundle(content_sha256="revoked-hash", commit_sha="commit-y")
        bundle.is_active = False
        bundle.save(update_fields=["is_active"])

        with self.assertRaises(PolicyBundleRejected):
            issue_capability(self._request(policy_bundle_sha256="revoked-hash"), now=NOW)


class DevDefaultBundleTests(TestCase):
    def test_get_or_create_dev_default_bundleは初回に発行し二回目は同じものを返す(self) -> None:
        first = policy_bundle.get_or_create_dev_default_bundle()
        second = policy_bundle.get_or_create_dev_default_bundle()

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PolicyBundle.objects.filter(content_sha256=first.content_sha256).count(), 1)

    def test_DEBUG_Falseで明示許可が無いと開発用bundleは拒否される(self) -> None:
        """セキュリティレビュー指摘対応: authority._signing_keyと同様、
        本番相当環境でD-04未決定用のfake bundleを気づかず使い続けられる
        抜け穴を塞ぐ。"""

        with override_settings(PMO_AUTHORITY_ALLOW_DEV_POLICY_BUNDLE=False):
            with self.assertRaises(InsecureDevBundleError):
                policy_bundle.get_or_create_dev_default_bundle()
