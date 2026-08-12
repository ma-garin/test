"""pmo_automation モデルの制約と、H-02（テナント越境拒否）を検証する。"""

from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.dashboard.models import Alert
from apps.pmo_automation.models import (
    ApprovalRequest,
    EvidenceBundle,
    PmoWorkItem,
    WorkItemState,
    WorkKind,
    WorkLink,
)
from apps.projects.models import Project

NOW = timezone.now()


class PmoWorkItemFactoryMixin:
    def _tenant(self, code: str) -> Tenant:
        return Tenant.objects.create(code=code, name=code.upper())

    def _project(self, tenant: Tenant, code: str = "p1") -> Project:
        return Project.objects.create(tenant=tenant, code=code, name="基幹刷新")

    def _work_item(self, tenant: Tenant, project: Project, **kwargs) -> PmoWorkItem:
        defaults = {
            "tenant": tenant,
            "project": project,
            "kind": WorkKind.DETECTION_TRIAGE,
            "source_type": "alert",
            "source_key": "alert-1",
            "dedupe_key": "alert:alert-1",
        }
        defaults.update(kwargs)

        return PmoWorkItem.objects.create(**defaults)


class PmoWorkItemConstraintTests(PmoWorkItemFactoryMixin, TestCase):
    """PA-01 受入条件: tenant と dedupe_key の有効 Work Item 一意制約。"""

    def test_同一テナントで同じdedupe_keyの有効Work_Itemは作れない(self) -> None:
        tenant = self._tenant("acme")
        project = self._project(tenant)
        self._work_item(tenant, project)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._work_item(tenant, project)

    def test_completedへ遷移してis_activeをFalseにすれば同じdedupe_keyを再作成できる(self) -> None:
        tenant = self._tenant("acme")
        project = self._project(tenant)
        first = self._work_item(tenant, project, state=WorkItemState.COMPLETED, is_active=False)

        second = self._work_item(tenant, project)

        self.assertNotEqual(first.pk, second.pk)
        self.assertTrue(PmoWorkItem.objects.filter(dedupe_key="alert:alert-1").count() == 2)

    def test_既存モデルを参照しないWorkLinkは作れない(self) -> None:
        tenant = self._tenant("acme")
        project = self._project(tenant)
        work_item = self._work_item(tenant, project)

        with self.assertRaises(IntegrityError), transaction.atomic():
            WorkLink.objects.create(work_item=work_item)

    def test_projectのテナントとtenantが異なると保存できない(self) -> None:
        tenant_a = self._tenant("tenant-a")
        tenant_b = self._tenant("tenant-b")
        project_b = self._project(tenant_b, code="p-b")

        with self.assertRaises(ValueError):
            PmoWorkItem.objects.create(
                tenant=tenant_a,
                project=project_b,
                kind=WorkKind.DETECTION_TRIAGE,
                source_type="alert",
                source_key="alert-x",
                dedupe_key="alert:alert-x",
            )

    def test_参照先を削除するとWorkLinkもCASCADE削除される(self) -> None:
        """SET_NULLだとworklink_at_least_one_target違反で削除自体が失敗する（レビュー指摘）ため、
        CASCADEで参照先の削除を妨げないことを確認する。"""

        tenant = self._tenant("acme")
        project = self._project(tenant)
        work_item = self._work_item(tenant, project)
        alert = Alert.objects.create(
            project=project,
            category=Alert.Category.SCHEDULE,
            title="遅延の疑い",
            detected_at=NOW,
        )
        link = WorkLink.objects.create(work_item=work_item, alert=alert)

        alert.delete()

        self.assertFalse(WorkLink.objects.filter(pk=link.pk).exists())


class TenantBoundaryTests(PmoWorkItemFactoryMixin, TestCase):
    """H-02: tenant A の実行が tenant B の Work Item / Evidence / Approval に影響しない。"""

    def setUp(self) -> None:
        self.tenant_a = self._tenant("tenant-a")
        self.tenant_b = self._tenant("tenant-b")
        self.project_a = self._project(self.tenant_a, code="p-a")
        self.project_b = self._project(self.tenant_b, code="p-b")
        # 同じ dedupe_key を異なるテナントで使っても、テナント単位で共存できる。
        self.work_item_a = self._work_item(self.tenant_a, self.project_a)
        self.work_item_b = self._work_item(self.tenant_b, self.project_b)
        self.evidence_b = EvidenceBundle.objects.create(
            work_item=self.work_item_b,
            source_type="alert",
            source_ref="alert-1",
            captured_at=NOW,
            expires_at=NOW + timedelta(hours=1),
            content_hash="hash-b",
        )
        self.approval_b = ApprovalRequest.objects.create(
            work_item=self.work_item_b,
            plan_version=1,
            requested_action="draft.create",
        )

    def test_同じdedupe_keyでも異なるテナントは共存する(self) -> None:
        self.assertEqual(self.work_item_a.dedupe_key, self.work_item_b.dedupe_key)
        self.assertNotEqual(self.work_item_a.tenant_id, self.work_item_b.tenant_id)

    def test_tenant_Aのクエリはtenant_Bのwork_itemを取得しない(self) -> None:
        scoped = PmoWorkItem.objects.filter(tenant=self.tenant_a)

        self.assertNotIn(self.work_item_b, scoped)
        self.assertEqual(list(scoped), [self.work_item_a])

    def test_tenant_Aの更新操作はtenant_Bのwork_itemを変更しない(self) -> None:
        before_state = self.work_item_b.state
        before_updated_at = self.work_item_b.updated_at

        PmoWorkItem.objects.filter(tenant=self.tenant_a).update(
            state=WorkItemState.ASSESSING, block_reason="A側の評価中"
        )

        self.work_item_b.refresh_from_db()
        self.assertEqual(self.work_item_b.state, before_state)
        self.assertEqual(self.work_item_b.updated_at, before_updated_at)

    def test_tenant_AのクエリはBの根拠と承認に0件しか触れない(self) -> None:
        touched_evidence = EvidenceBundle.objects.filter(work_item__tenant=self.tenant_a)
        touched_approval = ApprovalRequest.objects.filter(work_item__tenant=self.tenant_a)

        self.assertEqual(touched_evidence.count(), 0)
        self.assertEqual(touched_approval.count(), 0)
        # Bのレコードは変更されずそのまま残る。
        self.evidence_b.refresh_from_db()
        self.approval_b.refresh_from_db()
        self.assertEqual(self.evidence_b.content_hash, "hash-b")
        self.assertEqual(self.approval_b.requested_action, "draft.create")
