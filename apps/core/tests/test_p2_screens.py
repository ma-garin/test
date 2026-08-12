"""P2 画面の表示要件（UXP-35 設定 / UXP-48 行き止まりの解消）。

設定画面は「誰に何を頼めば変わるか」を出し、値そのものは絶対に出さない。
未実装ページ・画面マップ・ログインは、必ず次の操作と戻り先を持つ。
"""

from __future__ import annotations

from django.template.loader import get_template
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User

SECRET_OPENAI = {
    "API_KEY": "sk-leak-canary-0123456789",
    "ORG_ID": "org-leak-canary",
    "PROJECT_ID": "proj-leak-canary",
    "MODEL": "gpt-leak-canary",
    "EMBEDDING_MODEL": "emb-leak-canary",
}
SECRET_OLLAMA = {
    "BASE_URL": "http://ollama-leak-canary.internal:9999",
    "MODEL": "qwen-leak-canary",
    "EMBEDDING_MODEL": "nomic-leak-canary",
}


class P2SettingsScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="p2-core",
            email="p2-core@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

    # --- UXP-35 -----------------------------------------------------------

    def test_settings_shows_owner_change_path_and_apply_timing(self) -> None:
        """各ブロックに 状態・管理者・変更方法・反映タイミング が出る。"""

        html = self.client.get(reverse("core:settings")).content.decode()

        for label in ("状態", "管理者", "変更方法", "反映タイミング"):
            self.assertIn(label, html)

        # 変更方法は環境変数名と再起動手順で示す。
        self.assertIn("OPENAI_API_KEY", html)
        self.assertIn("OLLAMA_BASE_URL", html)
        self.assertIn("RAG_DEFAULT_TOP_K", html)
        self.assertIn("再起動後の最初のリクエストから", html)

    def test_settings_explains_why_user_cannot_edit_and_whom_to_ask(self) -> None:
        """編集できない理由と依頼先が読める。"""

        html = self.client.get(reverse("core:settings")).content.decode()

        self.assertIn("この画面は参照専用です", html)
        self.assertIn("画面から書き換える手段はありません", html)
        self.assertIn("システム管理者（インフラ担当）", html)
        self.assertIn("変更を依頼する先", html)

    @override_settings(OPENAI=SECRET_OPENAI, OLLAMA=SECRET_OLLAMA)
    def test_settings_never_renders_secret_values(self) -> None:
        """秘密情報（APIキー・組織ID・プロジェクトID）の生の値は画面に出さない。"""

        html = self.client.get(reverse("core:settings")).content.decode()

        for key in ("API_KEY", "ORG_ID", "PROJECT_ID"):
            self.assertNotIn(SECRET_OPENAI[key], html)

        # 出るのはマスク済みの値だけ（先頭 4 文字＋伏せ字）。
        self.assertIn(SECRET_OPENAI["API_KEY"][:4] + "*", html)
        # 代わりに、設定済みかどうかの状態を出す。
        self.assertIn("設定済み", html)

    @override_settings(OPENAI=SECRET_OPENAI, OLLAMA=SECRET_OLLAMA)
    def test_settings_keeps_existing_ai_setting_rows(self) -> None:
        """UXP-35 は追記であって置き換えではない。既存項目を消さない。"""

        response = self.client.get(reverse("core:settings"))

        labels = (
            "APIキー",
            "組織ID",
            "プロジェクトID",
            "回答モデル",
            "Embedding",
            "Ollama URL",
            "ローカルEmbedding",
        )

        for label in labels:
            self.assertContains(response, label)

        # 秘密でない設定値は、これまでどおり値そのものを出す。
        for value in (SECRET_OPENAI["MODEL"], SECRET_OPENAI["EMBEDDING_MODEL"], SECRET_OLLAMA["BASE_URL"]):
            self.assertContains(response, value)


class P2DeadEndScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="p2-deadend",
            email="p2-deadend@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )

    # --- UXP-48 -----------------------------------------------------------

    def test_not_implemented_has_return_target_and_next_action(self) -> None:
        """未実装ページに戻り先と次の操作がある。リンク先は実在する。"""

        source = get_template("pages/not_implemented.html").template.source

        self.assertIn("戻り先", source)
        self.assertIn("次の操作", source)
        self.assertIn("管制ダッシュボードへ戻る", source)
        self.assertIn("PMO相談を開く", source)

        # 参照している URL 名がすべて解決できること（行き止まりの先を壊さない）。
        for url_name in ("dashboard:control", "core:screen_map", "pmo:consultation", "projects:list"):
            self.assertTrue(reverse(url_name))
            self.assertIn(f"{{% url '{url_name}' %}}", source)

    def test_screen_map_shows_purpose_and_next_action(self) -> None:
        """画面マップに利用目的と次の操作が出る。"""

        self.client.force_login(self.user)
        html = self.client.get(reverse("core:screen_map")).content.decode()

        self.assertIn("この画面の利用目的", html)
        self.assertIn("次の操作", html)
        self.assertIn("管制ダッシュボードへ戻る", html)
        self.assertIn(f'href="{reverse("core:settings")}"', html)

    def test_login_failure_keeps_input_and_explains_retry_and_contact(self) -> None:
        """ログイン失敗時に入力を保持し、再試行方法と問い合わせ先を示す。"""

        response = self.client.post(reverse("accounts:login"), {"email": "not-an-email"})
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        # 入力したメールアドレスが消えていない。
        self.assertIn('value="not-an-email"', html)
        self.assertIn("入力したメールアドレスはそのまま残しています", html)
        self.assertIn("もう一度押してください", html)
        # 問い合わせ先と、システム側で対応しない範囲。
        self.assertIn("テナント管理者", html)
        self.assertIn("パスワード再発行はありません", html)
