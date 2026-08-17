"""ユーザーガイドの掲載内容が、実物とずれていないかを見る。

ガイドは放っておくと古くなる。カテゴリ名・画面のURL・スクリーンショットの
3点が実物と一致していること、トップに情報を積み直していないことを固定する。
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.guide import CATEGORY_GUIDES, SCREEN_GUIDES
from apps.core.navigation import NAVIGATION


class GuideContentTests(TestCase):
    def test_カテゴリの説明がナビゲーションの全カテゴリを覆う(self) -> None:
        navigation_keys = {section.key for section in NAVIGATION}
        guide_keys = {guide.key for guide in CATEGORY_GUIDES}

        self.assertEqual(guide_keys, navigation_keys)

    def test_カテゴリの入口が実在する画面を指す(self) -> None:
        for guide in CATEGORY_GUIDES:
            with self.subTest(category=guide.key):
                try:
                    reverse(guide.entry_url_name)
                except NoReverseMatch:  # pragma: no cover - 失敗時のみ
                    self.fail(f"入口の URL 名が解決できません: {guide.entry_url_name}")

    def test_画面ガイドが実在するカテゴリと画像を指す(self) -> None:
        category_keys = {guide.key for guide in CATEGORY_GUIDES}

        for screen in SCREEN_GUIDES:
            with self.subTest(screen=screen.label):
                reverse(screen.url_name)
                self.assertIn(screen.category, category_keys)

                # 画像が無いとガイドだけが壊れ、画面側は正常なので気づけない。
                path = Path(settings.BASE_DIR) / "static" / "img" / "guide" / screen.image
                self.assertTrue(
                    path.exists(),
                    f"{path} がありません。tools/capture_guide_shots.py で撮り直してください。",
                )


class GuideScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme-guide", name="ACME")
        self.user = User.objects.create_user(
            username="pmo-guide",
            email="pmo-guide@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.client.force_login(self.user)

    def test_トップは価値とカテゴリの入口だけを出す(self) -> None:
        response = self.client.get(reverse("core:user_guide"))

        self.assertContains(response, "遅延と品質の問題を、締切より前に見つける")
        self.assertContains(response, reverse("core:user_guide_category", args=["control"]))

        # 5W とスクリーンショットはカテゴリのページの担当。トップへ積み直さない。
        self.assertNotContains(response, "img/guide/")
        self.assertNotContains(response, "どこから")

    def test_カテゴリのページに5Wと実画面が出る(self) -> None:
        response = self.client.get(reverse("core:user_guide_category", args=["control"]))

        self.assertContains(response, "誰が")
        self.assertContains(response, "img/guide/control.png")

    def test_存在しないカテゴリは404(self) -> None:
        response = self.client.get(reverse("core:user_guide_category", args=["no-such"]))

        self.assertEqual(response.status_code, 404)
