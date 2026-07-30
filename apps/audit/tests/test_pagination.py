"""操作ログ・フィードバックのページング。

監査画面は「古い記録へ辿り着けること」が用途そのものなので、
先頭 200 件で打ち切ると機能として成立しない。ページで全件を辿れること、
評価分布の集計がページに引きずられないことを確認する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.audit.models import Feedback, OperationLog
from apps.core.pagination import PAGE_SIZE

#: 2 ページ目が必ず出る件数。
TOTAL_ROWS = 60

#: 事実誤認ありとして登録する件数。
FACT_ERROR_ROWS = 12


class AuditPaginationTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="audit-pager",
            email="audit-pager@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

    def test_operation_log_has_second_page(self) -> None:
        for index in range(TOTAL_ROWS):
            OperationLog.objects.create(
                tenant=self.tenant, user=self.user, action=f"操作{index}"
            )

        first = self.client.get(reverse("audit:operation_list"))
        second = self.client.get(reverse("audit:operation_list"), {"page": 2})

        self.assertEqual(first.context["page"].paginator.count, TOTAL_ROWS)
        self.assertEqual(len(first.context["logs"]), PAGE_SIZE)
        self.assertEqual(len(second.context["logs"]), TOTAL_ROWS - PAGE_SIZE)

    def test_feedback_stats_are_stable_across_pages(self) -> None:
        """明細はページで切っても、評価分布と事実誤認件数は全件から出す。"""

        for index in range(TOTAL_ROWS):
            Feedback.objects.create(
                tenant=self.tenant,
                user=self.user,
                rating=Feedback.Rating.GOOD,
                has_fact_error=index < FACT_ERROR_ROWS,
            )

        first = self.client.get(reverse("audit:feedback_list"))
        second = self.client.get(reverse("audit:feedback_list"), {"page": 2})

        self.assertEqual(len(first.context["feedbacks"]), PAGE_SIZE)
        self.assertEqual(len(second.context["feedbacks"]), TOTAL_ROWS - PAGE_SIZE)

        for response in (first, second):
            stats = response.context["stats"]
            self.assertEqual(stats.total, TOTAL_ROWS)
            self.assertEqual(stats.fact_error_count, FACT_ERROR_ROWS)
