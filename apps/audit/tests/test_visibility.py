"""監査データの閲覧範囲テスト。

操作ログとフィードバックには「誰が何をしたか」がそのまま並ぶ。テナントで
絞るだけでは、参照だけの利用者にも他人の操作・他人の評価コメントが全件
見えてしまう。他人の記録を見てよいのは承認権限を持つ立場（責任者）だけに
絞れていること、それ以外には自分の記録だけが返ることを確かめる。

投稿（`feedback_create`）は全員ができてよい。現場の声を集めるのが目的で、
投稿を責任者に限ると集まらなくなるため、ここも併せて検証する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.audit.models import Feedback, OperationLog


class AuditVisibilityTests(TestCase):
    #: 混入したら一目で分かるよう、他人の記録だけに現れる語を使う。
    OTHERS_ACTION = "他人だけが行った操作"
    OTHERS_COMMENT = "他人だけが書いた評価コメント"

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.viewer = self._user("viewer", Role.VIEWER)
        self.approver = self._user("pmo", Role.PMO)
        self.other = self._user("other", Role.CHANGE_MANAGER)

        OperationLog.objects.create(
            tenant=self.tenant, user=self.other, action=self.OTHERS_ACTION
        )
        OperationLog.objects.create(tenant=self.tenant, user=self.viewer, action="自分の操作")
        Feedback.objects.create(
            tenant=self.tenant,
            user=self.other,
            rating=Feedback.Rating.BAD,
            comment=self.OTHERS_COMMENT,
        )
        Feedback.objects.create(
            tenant=self.tenant,
            user=self.viewer,
            rating=Feedback.Rating.GOOD,
            comment="自分の評価コメント",
        )

    def _user(self, name: str, role: str) -> User:
        return User.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="test-password",
            tenant=self.tenant,
            role=role,
        )

    def test_参照専用ロールには他人の操作ログを見せない(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("audit:operation_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertNotContains(response, self.OTHERS_ACTION)
        self.assertContains(response, "自分の操作")

    def test_参照専用ロールには他人のフィードバックを見せない(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("audit:feedback_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stats"].total, 1)
        self.assertNotContains(response, self.OTHERS_COMMENT)

    def test_承認権限を持つ立場は全件を追える(self):
        """締めすぎていないこと。監査は全件を辿れなければ用を成さない。"""

        self.client.force_login(self.approver)

        operations = self.client.get(reverse("audit:operation_list"))
        feedbacks = self.client.get(reverse("audit:feedback_list"))

        self.assertEqual(operations.context["page"].paginator.count, 2)
        self.assertContains(operations, self.OTHERS_ACTION)
        self.assertEqual(feedbacks.context["stats"].total, 2)
        self.assertContains(feedbacks, self.OTHERS_COMMENT)

    def test_参照専用ロールでもフィードバックは投稿できる(self):
        self.client.force_login(self.viewer)

        before = Feedback.objects.count()
        response = self.client.post(
            reverse("audit:feedback_create"),
            {"rating": Feedback.Rating.GOOD, "comment": "参照だけの利用者からの声"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Feedback.objects.count(), before + 1)
