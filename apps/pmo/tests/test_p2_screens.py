"""P2 画面の表示要件（UXP-18 プロンプトライブラリ / UXP-19 教育支援）。

「押す前に何が起きるか読めるか」「次に開く画面へ辿れるか」を文字列で確かめる。
教育支援は個人の学習状況を持たないことも、テンプレート原文で確認する。
"""

from __future__ import annotations

from django.template.loader import get_template
from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.projects.models import Project


class P2PmoScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="p2-pmo",
            email="p2-pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.client.force_login(self.user)

    # --- UXP-18 -----------------------------------------------------------

    def test_prompt_library_shows_purpose_payload_and_primary_action(self) -> None:
        """各テンプレートに 用途・渡す内容・主操作 の3点が出る。"""

        html = self.client.get(reverse("pmo:prompt_library")).content.decode()

        for label in ("用途", "相談へ渡す内容", "主操作"):
            self.assertIn(label, html)

        self.assertIn("このテンプレートで相談", html)
        # 既定テンプレートの用途説明が本文として出ていること。
        self.assertIn("遅延の事実・原因・影響・打ち手を分けて整理させる。", html)

    def test_prompt_library_explains_handoff_before_the_click(self) -> None:
        """押す前に「何が渡り、修正できるか」が読める。"""

        html = self.client.get(reverse("pmo:prompt_library")).content.decode()

        self.assertIn("PMO相談画面が開きます", html)
        self.assertIn("送信はまだ行われません", html)
        self.assertIn("書き換え・追記してから、自分で送信します", html)
        # 説明はテンプレート一覧より前に置く（押した後に気づく順序にしない）。
        self.assertLess(html.index("送信はまだ行われません"), html.index("<table"))

    # --- UXP-19 -----------------------------------------------------------

    def test_education_step_names_link_to_existing_screens(self) -> None:
        """ステップ名そのものが既存画面へのリンクになっている。"""

        html = self.client.get(reverse("pmo:education")).content.decode()

        expected = (
            ("dashboard:control", "1. 状況を掴む"),
            ("projects:list", "2. 案件を読む"),
            ("pmo:prompt_library", "3. 相談する"),
            ("pmo:planning", "4. 計画に落とす"),
            ("pmo:deliverables", "5. 報告を作る"),
            ("pmo:approvals", "6. 承認する"),
        )

        for url_name, step in expected:
            anchor = f'<a class="pb-t" href="{reverse(url_name)}">{step}</a>'
            self.assertIn(anchor, html)

    def test_education_describes_each_screen_and_keeps_no_personal_progress(self) -> None:
        """各リンクに一文の説明が付き、個人の進捗は持たない。"""

        response = self.client.get(reverse("pmo:education"))
        html = response.content.decode()

        # 6 ステップすべてに「〜画面です。」の一文がある。
        self.assertGreaterEqual(html.count("画面です。"), 6)
        self.assertIn("誰がどのステップまで進んだかは記録も表示もしません", html)

        # 進捗語をテンプレート原文に持ち込まない（他画面の文言を拾わないよう原文で見る）。
        source = get_template("pages/pmo_education.html").template.source

        for word in ("完了", "未実施", "済み", "チェックボックス"):
            self.assertNotIn(word, source)

        # ビューが個人の進捗をコンテキストへ載せていないこと。
        for key in response.context.keys():
            self.assertNotIn("progress", str(key).lower())
