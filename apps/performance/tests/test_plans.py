"""期初計画と期中変更計画の適用。

期中変更が過去へ遡らないこと、変更に含まれない組織が前の版を引き継ぐことを
固定する。ここが崩れると、変更と無関係な組織の計画が期中で消える。
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from apps.performance.constants import PlanKind, PlanStatus
from apps.performance.services import aggregation, plans
from apps.performance.tests import factories


class PlanVersionTests(TestCase):
    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.year = factories.make_year(self.tenant)
        self.units = factories.make_tree(self.tenant)

        self.initial = factories.make_version(self.tenant, self.year)
        self.revised = factories.make_version(
            self.tenant,
            self.year,
            kind=PlanKind.REVISED,
            revision=1,
            effective_from=date(2026, 10, 1),
        )

        self.april, self.october = date(2026, 4, 1), date(2026, 10, 1)

        for month in (self.april, self.october):
            factories.add_plan(self.initial, self.units["sec"], month, 1000)
            factories.add_plan(self.initial, self.units["prj"], month, 500)

        # 期中変更は営業課だけを見直す。プロジェクトには行を置かない。
        factories.add_plan(self.revised, self.units["sec"], self.october, 800)

    def test_revision_does_not_apply_to_earlier_months(self) -> None:
        effective = plans.effective_figures(self.year)

        april = effective[(self.units["sec"].pk, None, self.april)]

        self.assertEqual(april.figure.revenue, 1000)
        self.assertEqual(april.version.pk, self.initial.pk)

    def test_revision_applies_from_effective_month(self) -> None:
        effective = plans.effective_figures(self.year)

        october = effective[(self.units["sec"].pk, None, self.october)]

        self.assertEqual(october.figure.revenue, 800)
        self.assertFalse(october.is_carried_over)

    def test_untouched_org_carries_previous_version(self) -> None:
        effective = plans.effective_figures(self.year)

        october = effective[(self.units["prj"].pk, None, self.october)]

        self.assertEqual(october.figure.revenue, 500)
        self.assertEqual(october.version.pk, self.initial.pk)
        self.assertTrue(october.is_carried_over)

    def test_draft_version_is_not_applied(self) -> None:
        self.revised.status = PlanStatus.DRAFT
        self.revised.save(update_fields=["status"])

        effective = plans.effective_figures(self.year)

        self.assertEqual(
            effective[(self.units["sec"].pk, None, self.october)].figure.revenue, 1000
        )

    def test_current_version_depends_on_month(self) -> None:
        self.assertEqual(plans.current_version(self.year, self.april).pk, self.initial.pk)
        self.assertEqual(plans.current_version(self.year, self.october).pk, self.revised.pk)

    def test_initial_plan_is_kept_for_comparison(self) -> None:
        report = aggregation.build_report(
            self.year, list(self.units.values()), [self.april, self.october]
        )
        summary = report.for_unit(self.units["sec"])

        # 課の合計は配下プロジェクト（各月 500）を含む。
        # 現行計画: 課 1000+800 とPJ 500+500 = 2800
        # 期初計画: 課 1000+1000 とPJ 500+500 = 3000
        self.assertEqual(summary.total_plan.revenue, 2800)
        self.assertEqual(summary.total_initial.revenue, 3000)
        self.assertEqual(summary.plan_revision.revenue, -200)
