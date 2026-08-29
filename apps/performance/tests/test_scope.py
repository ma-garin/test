"""参照・編集範囲。

ラインマネージャーが見てよいのは自分の組織と配下だけで、他テナントは
存在ごと見えない。ここが崩れると他部門の計数が漏れる。
"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.constants import Role
from apps.performance import selectors
from apps.performance.constants import OrgLevel
from apps.performance.tests import factories


class ScopeTests(TestCase):
    def setUp(self) -> None:
        self.tenant = factories.make_tenant("t1")
        self.other = factories.make_tenant("t2")

        self.manager = factories.make_user(self.tenant, "manager@example.com")
        self.peer = factories.make_user(self.tenant, "peer@example.com")
        self.admin = factories.make_user(
            self.tenant, "admin@example.com", role=Role.TENANT_ADMIN
        )

        self.units = factories.make_tree(self.tenant, manager=self.manager)
        self.other_units = factories.make_tree(self.other)

        # 課だけを持つ別のマネージャー。部は見えてはいけない。
        self.section_manager = factories.make_user(self.tenant, "sec@example.com")
        self.units["sec"].manager = self.section_manager
        self.units["sec"].save(update_fields=["manager"])

    def test_manager_sees_own_subtree(self) -> None:
        visible = selectors.visible_org_ids(self.manager, self.tenant)

        self.assertEqual(visible, {unit.pk for unit in self.units.values()})

    def test_section_manager_does_not_see_parent(self) -> None:
        visible = selectors.visible_org_ids(self.section_manager, self.tenant)

        self.assertEqual(visible, {self.units["sec"].pk, self.units["prj"].pk})
        self.assertNotIn(self.units["div"].pk, visible)

    def test_unrelated_user_sees_nothing(self) -> None:
        self.assertEqual(selectors.visible_org_ids(self.peer, self.tenant), set())

    def test_member_sees_own_org_only(self) -> None:
        factories.make_member(self.tenant, self.units["prj"], code="E9", user=self.peer)

        visible = selectors.visible_org_ids(self.peer, self.tenant)

        self.assertEqual(visible, {self.units["prj"].pk})
        self.assertFalse(selectors.can_edit_org(self.peer, self.units["prj"]))

    def test_tenant_admin_sees_all_units_of_own_tenant_only(self) -> None:
        visible = selectors.visible_org_ids(self.admin, self.tenant)

        self.assertEqual(visible, {unit.pk for unit in self.units.values()})
        for unit in self.other_units.values():
            self.assertNotIn(unit.pk, visible)

    def test_edit_scope_matches_managed_subtree(self) -> None:
        managed = selectors.managed_org_ids(self.section_manager, self.tenant)

        self.assertEqual(managed, {self.units["sec"].pk, self.units["prj"].pk})
        self.assertTrue(selectors.can_edit_org(self.section_manager, self.units["prj"]))
        self.assertFalse(selectors.can_edit_org(self.section_manager, self.units["div"]))

    def test_viewer_role_cannot_edit_even_when_manager(self) -> None:
        viewer = factories.make_user(self.tenant, "viewer@example.com", role=Role.VIEWER)
        unit = factories.make_org(self.tenant, "div2", OrgLevel.DIVISION, manager=viewer)

        self.assertIn(unit.pk, selectors.visible_org_ids(viewer, self.tenant))
        self.assertEqual(selectors.managed_org_ids(viewer, self.tenant), set())

    def test_members_are_limited_to_visible_orgs(self) -> None:
        mine = factories.make_member(self.tenant, self.units["sec"], code="E1")
        factories.make_member(self.other, self.other_units["sec"], code="E2")

        members = selectors.members_for(self.manager, self.tenant)

        self.assertEqual([member.pk for member in members], [mine.pk])
