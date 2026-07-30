"""フィードバック集計の検証。

集計値そのものより「テナントを越えないこと」「不正な絞り込み値で落ちないこと」を
重点的に確認する。この 2 つが漏れると監査画面としての信頼が崩れるため。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.audit.models import Feedback
from apps.audit.selectors import feedbacks_for
from apps.audit.services import feedback_stats


class FeedbackStatsTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other_tenant = Tenant.objects.create(code="globex", name="Globex")
        self.user = User.objects.create_user(
            username="audit-user",
            email="audit-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

        Feedback.objects.create(tenant=self.tenant, user=self.user, rating=Feedback.Rating.GOOD)
        Feedback.objects.create(tenant=self.tenant, user=self.user, rating=Feedback.Rating.GOOD)
        Feedback.objects.create(
            tenant=self.tenant,
            user=self.user,
            rating=Feedback.Rating.BAD,
            has_fact_error=True,
        )
        Feedback.objects.create(tenant=self.tenant, rating=Feedback.Rating.NEUTRAL)
        Feedback.objects.create(
            tenant=self.other_tenant,
            rating=Feedback.Rating.BAD,
            comment="他テナントの評価",
            has_fact_error=True,
        )

    def test_評価分布と事実誤認を集計する(self):
        stats = feedback_stats.summarize(feedbacks_for(self.user, self.tenant))

        self.assertEqual(stats.total, 4)
        self.assertEqual(stats.good_count, 2)
        self.assertEqual(stats.bad_count, 1)
        self.assertEqual(stats.good_percent, 50)
        self.assertEqual(stats.fact_error_count, 1)
        self.assertEqual(stats.fact_ok_count, 3)
        self.assertEqual(stats.fact_error_tone, "r")

    def test_他テナントのフィードバックを混ぜない(self):
        response = self.client.get(reverse("audit:feedback_list") + "?period=0")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "他テナントの評価")
        self.assertEqual(response.context["stats"].total, 4)

    def test_期間で絞り込む(self):
        old = Feedback.objects.create(tenant=self.tenant, rating=Feedback.Rating.GOOD)
        Feedback.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=120))

        criteria = feedback_stats.FeedbackCriteria(days=30)
        narrowed = feedback_stats.apply_criteria(feedbacks_for(self.user, self.tenant), criteria)

        self.assertEqual(narrowed.count(), 4)
        self.assertEqual(feedbacks_for(self.user, self.tenant).count(), 5)

    def test_利用者で絞り込む(self):
        criteria = feedback_stats.FeedbackCriteria(days=0, user_id=self.user.pk)
        narrowed = feedback_stats.apply_criteria(feedbacks_for(self.user, self.tenant), criteria)

        self.assertEqual(narrowed.count(), 3)

    def test_不正な絞り込み値は既定へ倒す(self):
        criteria = feedback_stats.parse_criteria({"period": "abc", "user": "'; DROP TABLE"})

        self.assertEqual(criteria.days, feedback_stats.DEFAULT_PERIOD_DAYS)
        self.assertIsNone(criteria.user_id)

    def test_件数0でも割合計算で落ちない(self):
        Feedback.objects.all().delete()
        stats = feedback_stats.summarize(feedbacks_for(self.user, self.tenant))

        self.assertEqual(stats.total, 0)
        self.assertEqual(stats.good_percent, 0)
        self.assertEqual(stats.fact_error_tone, "g")

    def test_投稿者の選択肢はテナント内に限る(self):
        options = feedback_stats.reporter_options(feedbacks_for(self.user, self.tenant))

        self.assertEqual([option["id"] for option in options], [self.user.pk])
        self.assertEqual(options[0]["count"], 3)
