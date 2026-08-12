"""GE-01: 型付き関連の不変条件を、保存できる／できないという外部挙動で固定する。

「全リンクにテナント・案件・関係型・状態・出所があり、不正な型や別テナントの接続を
保存できない」を確認する。
"""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.graph.models import Component, Feature, WorkLink
from apps.graph.ontology import (
    ALLOWED_ENDPOINTS,
    LinkState,
    Provenance,
    RelationType,
    allowed_targets,
    is_allowed,
)
from apps.projects.models import Defect, Project, Severity


class OntologyTableTests(TestCase):
    def test_every_relation_type_is_declared(self):
        self.assertEqual(set(ALLOWED_ENDPOINTS), {r.value for r in RelationType})

    def test_free_text_relation_is_not_allowed(self):
        self.assertFalse(is_allowed("looks_similar_to", "graph.component", "graph.feature"))

    def test_direction_matters(self):
        self.assertTrue(is_allowed(RelationType.IMPLEMENTS, "graph.component", "graph.feature"))
        self.assertFalse(is_allowed(RelationType.IMPLEMENTS, "graph.feature", "graph.component"))

    def test_allowed_targets_lists_only_declared_pairs(self):
        targets = allowed_targets(RelationType.IMPACTS, "projects.defect")
        self.assertIn("graph.feature", targets)
        self.assertNotIn("documents.document", targets)


class WorkLinkValidationTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other_tenant = Tenant.objects.create(code="beta", name="BETA")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="pw",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.other_project = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")
        self.foreign_project = Project.objects.create(
            tenant=self.other_tenant, code="x1", name="他テナント案件"
        )

        self.feature = Feature.objects.create(project=self.project, name="受注登録")
        self.component = Component.objects.create(
            project=self.project, name="注文API", kind=Component.Kind.API
        )
        self.defect = Defect.objects.create(
            project=self.project, title="金額がずれる", severity=Severity.CRITICAL
        )

    def _link(self, **overrides) -> WorkLink:
        defaults = {
            "relation_type": RelationType.IMPLEMENTS,
            "from_object": self.component,
            "to_object": self.feature,
            "provenance": Provenance.MANUAL,
        }
        defaults.update(overrides)
        return WorkLink(**defaults)

    def test_allowed_link_is_saved_and_scoped_to_project(self):
        link = self._link()
        link.save()
        self.assertEqual(link.project, self.project)
        self.assertEqual(link.state, LinkState.CANDIDATE)

    def test_disallowed_relation_type_cannot_be_saved(self):
        with self.assertRaises(ValidationError):
            self._link(relation_type="looks_similar_to").save()

    def test_disallowed_endpoint_pair_cannot_be_saved(self):
        """方向が逆の組み合わせは保存できない。"""
        with self.assertRaises(ValidationError):
            self._link(from_object=self.feature, to_object=self.component).save()

    def test_cross_tenant_link_cannot_be_saved(self):
        foreign_feature = Feature.objects.create(project=self.foreign_project, name="別テナント機能")
        with self.assertRaises(ValidationError) as ctx:
            self._link(to_object=foreign_feature, is_cross_project=True).save()
        self.assertIn("テナント", str(ctx.exception))

    def test_cross_project_link_requires_explicit_flag(self):
        other_feature = Feature.objects.create(project=self.other_project, name="別案件機能")
        with self.assertRaises(ValidationError):
            self._link(to_object=other_feature).save()

        link = self._link(to_object=other_feature, is_cross_project=True)
        link.save()
        self.assertTrue(link.is_cross_project)

    def test_provenance_is_required(self):
        with self.assertRaises(ValidationError):
            self._link(provenance="").save()

    def test_ai_candidate_cannot_be_confirmed_without_reviewer(self):
        with self.assertRaises(ValidationError) as ctx:
            self._link(
                provenance=Provenance.AI_CANDIDATE, state=LinkState.CONFIRMED
            ).save()
        self.assertIn("人が確認するまで", str(ctx.exception))

    def test_external_id_link_may_be_confirmed_automatically(self):
        link = self._link(provenance=Provenance.EXTERNAL_ID, state=LinkState.CONFIRMED)
        link.save()
        self.assertEqual(link.state, LinkState.CONFIRMED)

    def test_confirm_records_reviewer_and_time(self):
        link = self._link(provenance=Provenance.AI_CANDIDATE)
        link.save()
        link.confirm(self.user, reason="PMが確認")
        self.assertEqual(link.state, LinkState.CONFIRMED)
        self.assertEqual(link.confirmed_by, self.user)
        self.assertIsNotNone(link.confirmed_at)
        self.assertEqual(link.review_reason, "PMが確認")

    def test_reject_keeps_the_edge_for_audit(self):
        link = self._link(provenance=Provenance.AI_CANDIDATE)
        link.save()
        link.reject(self.user, reason="別機能だった")
        self.assertEqual(WorkLink.objects.filter(pk=link.pk).count(), 1)
        self.assertEqual(link.state, LinkState.REJECTED)

    def test_self_link_is_rejected(self):
        with self.assertRaises(ValidationError):
            WorkLink(
                relation_type=RelationType.DEPENDS_ON,
                from_object=self.feature,
                to_object=self.feature,
                provenance=Provenance.MANUAL,
            ).save()

    def test_duplicate_edge_is_rejected(self):
        self._link().save()
        with self.assertRaises(ValidationError):
            self._link().save()

    def test_confirmed_queryset_excludes_candidates(self):
        self._link(provenance=Provenance.AI_CANDIDATE).save()
        confirmed = self._link(
            relation_type=RelationType.IMPACTS,
            from_object=self.defect,
            to_object=self.feature,
            provenance=Provenance.EXTERNAL_ID,
            state=LinkState.CONFIRMED,
        )
        confirmed.save()
        self.assertEqual([link.pk for link in WorkLink.objects.confirmed()], [confirmed.pk])

    def test_missing_endpoint_is_reported(self):
        link = WorkLink(
            relation_type=RelationType.IMPLEMENTS,
            from_content_type=ContentType.objects.get_for_model(Component),
            from_object_id=self.component.pk,
            to_content_type=ContentType.objects.get_for_model(Feature),
            to_object_id=self.feature.pk,
            provenance=Provenance.MANUAL,
        )
        self.feature.delete()
        with self.assertRaises(ValidationError):
            link.save()
