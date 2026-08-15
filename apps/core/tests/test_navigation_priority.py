"""サイドバーの項目表示。

以前は使用頻度の低い画面を「その他」へ畳んでいたが、利用者が自分の使う画面を
探せなくなるため廃止した。ここでは**全項目が並ぶこと**を固定する。

畳んだ状態（アイコン表示）で画面に収まるかは高さの問題であり、
項目を隠すことで解決しない。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.navigation import NAVIGATION, NavItem, all_items, navigation_for

#: `static/fonts/MaterialSymbolsOutlined-subset.woff2` に収録済みのアイコン。
#: ここに無い名前を使うと、画面にその文字列がそのまま出る。
#: 増やすにはフォントの再サブセット化（外部取得）が要るため、安易に足さない。
BUNDLED_ICONS = frozenset({"dashboard", "support_agent", "folder", "history", "settings"})


class NavigationItemsTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="nav", name="NAV")
        self.admin = User.objects.create_user(
            username="nav-admin",
            email="nav-admin@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )

    def test_全項目が返る(self):
        """権限で隠れるもの以外は、1 つも畳まれずに返ること。"""

        sections = navigation_for(self.admin)
        returned = sum(len(section.items) for section in sections)

        self.assertEqual(returned, len(all_items()))

    def test_定義した項目が欠けない(self):
        """セクションごとの件数が定義と一致すること。"""

        by_key = {section.key: section for section in navigation_for(self.admin)}

        for defined in NAVIGATION:
            with self.subTest(section=defined.key):
                self.assertEqual(len(by_key[defined.key].items), len(defined.items))

    def test_ロール限定を宣言した項目だけが除かれる(self):
        """絞り込みの仕組みそのものを固定する。

        現時点でロール限定を宣言している項目は無い（AI設定は利用者ごとに API キーを
        持てるようになり、全ロールへ開いた）。宣言が無くなったからといって仕組みごと
        外すと、次にロール限定の画面を足したときに素通しになる。
        """

        viewer = User.objects.create_user(
            username="nav-viewer",
            email="nav-viewer@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.VIEWER,
        )
        admin_only = NavItem("secret", "管理限定", "core:settings", roles=(Role.TENANT_ADMIN,))
        open_item = NavItem("open", "全員", "core:settings")

        self.assertFalse(admin_only.is_visible_to(viewer))
        self.assertTrue(open_item.is_visible_to(viewer))

        visible = {item.key for s in navigation_for(viewer) for item in s.items}

        self.assertIn("control_dashboard", visible)
        self.assertIn("settings", visible)

    def test_項目の重複が無い(self):
        keys = [item.key for item in all_items()]

        self.assertEqual(len(keys), len(set(keys)))

    def test_親カテゴリには必ず視覚的な識別子がある(self):
        """親レールを無印へ戻さない。

        アイコンは同梱したサブセットに収録された名前しか描けない。
        収録外の名前を書くと文字列がそのまま出るため、カテゴリを増やすときは
        アイコンではなく 2 文字の識別子を持たせる。どちらも無い状態は許さない。
        """

        markers = [section.icon or section.code for section in NAVIGATION]

        self.assertNotIn("", markers)
        self.assertEqual(len(markers), len(set(markers)), "識別子が重複している")

    def test_アイコンは同梱サブセットに収録された名前だけを使う(self):
        used = {section.icon for section in NAVIGATION if section.icon}

        self.assertLessEqual(used, BUNDLED_ICONS, f"未収録のアイコン: {used - BUNDLED_ICONS}")


class NavigationRenderTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="nav2", name="NAV2")
        self.user = User.objects.create_user(
            username="nav-render",
            email="nav-render@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

    def test_サイドバーに畳み込みが出ない(self):
        """「その他」は廃止した。復活していないことを固定する。"""

        response = self.client.get(reverse("dashboard:control"))

        self.assertNotContains(response, "その他")
        self.assertNotContains(response, "sb-more")

    def test_使用頻度の低い画面もサイドバーに出る(self):
        """以前「その他」へ畳んでいた画面が、直接見えること。"""

        response = self.client.get(reverse("dashboard:control"))

        for label in ("KPI・効果測定", "PoC合否判定", "操作ログ", "同期履歴", "ひな型一覧"):
            with self.subTest(label=label):
                self.assertContains(response, label)

    def test_親カテゴリにはローカルのMaterial_Symbolsを使う(self):
        response = self.client.get(reverse("dashboard:control"))

        self.assertContains(response, 'class="sb-rail-item active"')
        self.assertContains(response, 'data-parent-link="true"')
        self.assertContains(response, f'href="{reverse("dashboard:control")}"')
        self.assertContains(response, 'class="material-symbols-outlined sb-rail-icon"')
        self.assertContains(response, 'class="sb-rail-label">進捗</span>')
        for icon in sorted(BUNDLED_ICONS):
            with self.subTest(icon=icon):
                self.assertContains(response, f">{icon}</span>")

    def test_フォント未収録のカテゴリはインラインSVGで描く(self):
        """アイコンを増やすためにフォントを取りに行かない。

        同梱サブセットに無いカテゴリは SVG で描く。外部取得が不要で、
        オフライン同梱の前提を崩さずにアイコンを増やせる。
        """

        response = self.client.get(reverse("dashboard:control"))

        for section in NAVIGATION:
            if section.icon:
                continue
            with self.subTest(section=section.key):
                self.assertContains(response, 'class="sb-rail-svg"')
                # 無印にも、文字の代替表示にもしない。
                self.assertNotContains(
                    response, f'class="sb-rail-code" aria-hidden="true">{section.code}<'
                )

    def test_全カテゴリがアイコンで描かれる(self):
        """レールに文字だけのカテゴリを残さない。"""

        response = self.client.get(reverse("dashboard:control"))
        drawn = response.content.decode().count('class="material-symbols-outlined sb-rail-icon"')
        drawn += response.content.decode().count('class="sb-rail-svg"')

        self.assertEqual(drawn, len(NAVIGATION))

    def test_親レール用の名称は省略形を全カテゴリに定義する(self):
        self.assertEqual(
            [section.short_label for section in NAVIGATION],
            ["進捗", "品質", "評価", "PMO", "ナレッジ", "監査", "設定"],
        )

    def test_管理画面は専用のナビゲーション状態で描画する(self):
        response = self.client.get(reverse("core:settings"))

        self.assertContains(response, 'class="shell shell--admin"')
        self.assertContains(response, 'class="ph-context ph-context-admin"')
        self.assertContains(response, "管理対象")

    def test_子メニューを隠した後もレールから表示し直せる(self):
        """オーバーレイを閉じたあとに、子メニューへ戻れなくしない。"""

        response = self.client.get(reverse("dashboard:control"))

        self.assertContains(response, 'id="nav-rail-toggle"')
        self.assertContains(response, 'data-nav-toggle')
        self.assertContains(response, 'aria-controls="nav-panel"')
