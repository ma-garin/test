"""利用者ごとの AI 設定。

守りたいのは次の3つ。

1. ロールに関係なく、全員が自分の API 設定を持てる（要件そのもの）
2. 生の API キーが画面・HTML・ログのどこにも出ない
3. 「空欄＝上位に従う」が本当に効く（上位を変えたのに古い値を握らない）
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.models import TenantAISetting, UserAISetting
from apps.core.services.ai_settings import effective_config, masked_ai_settings
from apps.core.services.secrets import decrypt, encrypt

#: 画面に出てはいけない生の値。テスト中はこの文字列を全文検索する。
RAW_KEY = "sk-personal-key-must-never-be-rendered"

#: 全ロール。1つでも設定できないロールがあれば要件を満たしていない。
ALL_ROLES = [value for value, _ in Role.choices]


def make_user(tenant, role, suffix=""):
    return User.objects.create_user(
        username=f"{role}{suffix}",
        email=f"{role}{suffix}@example.com",
        password="test-password",
        tenant=tenant,
        role=role,
    )


class SecretStorageTests(TestCase):
    def test_暗号化した値は原文を含まない(self):
        encrypted = encrypt(RAW_KEY)

        self.assertNotIn(RAW_KEY, encrypted)
        self.assertTrue(encrypted.startswith("enc:v1:"))

    def test_復号すると原文へ戻る(self):
        self.assertEqual(decrypt(encrypt(RAW_KEY)), RAW_KEY)

    def test_空文字はそのまま扱う(self):
        self.assertEqual(encrypt(""), "")
        self.assertEqual(decrypt(""), "")

    def test_二重に暗号化しない(self):
        once = encrypt(RAW_KEY)

        self.assertEqual(encrypt(once), once)

    def test_壊れた暗号文は例外にせず空を返す(self):
        # 鍵を回した後でも設定画面を開いて入れ直せる必要がある。
        self.assertEqual(decrypt("enc:v1:not-a-valid-token"), "")


class EffectiveConfigTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = make_user(self.tenant, Role.VIEWER)

    def test_設定が無ければ環境変数の値になる(self):
        config = effective_config(user=self.user, tenant=self.tenant)

        self.assertEqual(config.provider, "local_hash")
        self.assertEqual(config.sources["provider"], "env")

    def test_テナント既定が環境変数を上書きする(self):
        TenantAISetting.objects.create(tenant=self.tenant, provider="openai", openai_api_key=RAW_KEY)

        config = effective_config(user=self.user, tenant=self.tenant)

        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.openai_api_key, RAW_KEY)
        self.assertEqual(config.sources["openai_api_key"], "tenant")

    def test_個人設定がテナント既定を上書きする(self):
        TenantAISetting.objects.create(tenant=self.tenant, provider="ollama")
        UserAISetting.objects.create(user=self.user, provider="openai", openai_api_key=RAW_KEY)

        config = effective_config(user=self.user, tenant=self.tenant)

        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.sources["provider"], "user")

    def test_空欄の項目は上位を引き継ぐ(self):
        TenantAISetting.objects.create(tenant=self.tenant, provider="openai", openai_model="gpt-tenant")
        # 個人側はモデルだけ変え、プロバイダは空のまま。
        UserAISetting.objects.create(user=self.user, openai_model="gpt-personal")

        config = effective_config(user=self.user, tenant=self.tenant)

        self.assertEqual(config.openai_model, "gpt-personal")
        self.assertEqual(config.sources["openai_model"], "user")
        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.sources["provider"], "tenant")

    def test_無効化した個人設定は効かない(self):
        TenantAISetting.objects.create(tenant=self.tenant, provider="ollama")
        UserAISetting.objects.create(user=self.user, provider="openai", is_active=False)

        self.assertEqual(effective_config(user=self.user, tenant=self.tenant).provider, "ollama")

    def test_テナントが個人設定を禁止すると個人の値を使わない(self):
        TenantAISetting.objects.create(
            tenant=self.tenant, provider="ollama", allow_personal_credentials=False
        )
        UserAISetting.objects.create(user=self.user, provider="openai", openai_api_key=RAW_KEY)

        config = effective_config(user=self.user, tenant=self.tenant)

        self.assertEqual(config.provider, "ollama")
        self.assertNotEqual(config.openai_api_key, RAW_KEY)

    def test_利用者が違えば設定も分かれる(self):
        other = make_user(self.tenant, Role.PMO)
        UserAISetting.objects.create(user=self.user, openai_model="mine")
        UserAISetting.objects.create(user=other, openai_model="theirs")

        self.assertEqual(effective_config(user=self.user).openai_model, "mine")
        self.assertEqual(effective_config(user=other).openai_model, "theirs")

    def test_マスク済み設定に生のキーを含めない(self):
        UserAISetting.objects.create(user=self.user, provider="openai", openai_api_key=RAW_KEY)

        masked = masked_ai_settings(user=self.user, tenant=self.tenant)

        self.assertNotIn(RAW_KEY, str(masked))
        self.assertTrue(masked["openai"]["api_key"].startswith("sk-p"))


class SettingsScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")

    def test_全ロールが設定画面を開ける(self):
        for role in ALL_ROLES:
            with self.subTest(role=role):
                self.client.force_login(make_user(self.tenant, role, suffix="-open"))

                self.assertEqual(self.client.get(reverse("core:settings")).status_code, 200)

    def test_全ロールのサイドバーに設定画面が出る(self):
        for role in ALL_ROLES:
            with self.subTest(role=role):
                self.client.force_login(make_user(self.tenant, role, suffix="-nav"))
                response = self.client.get(reverse("dashboard:control"))

                self.assertContains(response, reverse("core:settings"))

    def test_全ロールが自分のAPIキーを保存できる(self):
        for role in ALL_ROLES:
            with self.subTest(role=role):
                user = make_user(self.tenant, role, suffix="-save")
                self.client.force_login(user)

                response = self.client.post(
                    reverse("core:settings"),
                    {"scope": "user", "is_active": "on", "provider": "openai", "openai_api_key": RAW_KEY},
                )

                self.assertEqual(response.status_code, 302)
                self.assertEqual(effective_config(user=user).openai_api_key, RAW_KEY)

    def test_保存したキーは画面へ出さない(self):
        user = make_user(self.tenant, Role.VIEWER, suffix="-mask")
        self.client.force_login(user)
        self.client.post(
            reverse("core:settings"),
            {"scope": "user", "is_active": "on", "provider": "openai", "openai_api_key": RAW_KEY},
        )

        response = self.client.get(reverse("core:settings"))

        self.assertNotContains(response, RAW_KEY)
        self.assertContains(response, "sk-p")

    def test_保存したキーはDBに平文で残らない(self):
        user = make_user(self.tenant, Role.PMO, suffix="-db")
        self.client.force_login(user)
        self.client.post(
            reverse("core:settings"),
            {"scope": "user", "is_active": "on", "provider": "openai", "openai_api_key": RAW_KEY},
        )

        stored = UserAISetting.objects.get(user=user)

        self.assertNotIn(RAW_KEY, stored.openai_api_key)
        self.assertEqual(stored.secret("openai_api_key"), RAW_KEY)

    def test_空欄で保存してもキーは消えない(self):
        user = make_user(self.tenant, Role.PMO, suffix="-keep")
        self.client.force_login(user)
        self.client.post(
            reverse("core:settings"),
            {"scope": "user", "is_active": "on", "provider": "openai", "openai_api_key": RAW_KEY},
        )
        # モデル名だけ直す。キー欄は空のまま。
        self.client.post(
            reverse("core:settings"),
            {
                "scope": "user",
                "is_active": "on",
                "provider": "openai",
                "openai_api_key": "",
                "openai_model": "gpt-4.1",
            },
        )

        config = effective_config(user=user)

        self.assertEqual(config.openai_api_key, RAW_KEY)
        self.assertEqual(config.openai_model, "gpt-4.1")

    def test_削除を選んだときだけキーを消す(self):
        user = make_user(self.tenant, Role.PMO, suffix="-clear")
        self.client.force_login(user)
        self.client.post(
            reverse("core:settings"),
            {"scope": "user", "is_active": "on", "provider": "openai", "openai_api_key": RAW_KEY},
        )
        self.client.post(
            reverse("core:settings"),
            {"scope": "user", "is_active": "on", "provider": "", "clear_openai_api_key": "on"},
        )

        self.assertEqual(UserAISetting.objects.get(user=user).secret("openai_api_key"), "")

    def test_キー無しでOpenAIを選ぶと保存させない(self):
        user = make_user(self.tenant, Role.PMO, suffix="-invalid")
        self.client.force_login(user)

        response = self.client.post(
            reverse("core:settings"), {"scope": "user", "is_active": "on", "provider": "openai"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "APIキーが必要です")
        self.assertFalse(UserAISetting.objects.filter(user=user).exists())

    def test_管理権限が無ければテナント既定を保存できない(self):
        user = make_user(self.tenant, Role.VIEWER, suffix="-tenant-ng")
        self.client.force_login(user)

        response = self.client.post(
            reverse("core:settings"), {"scope": "tenant", "is_active": "on", "provider": "ollama"}
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(TenantAISetting.objects.filter(tenant=self.tenant).exists())

    def test_テナント管理者はテナント既定を保存できる(self):
        user = make_user(self.tenant, Role.TENANT_ADMIN, suffix="-tenant-ok")
        self.client.force_login(user)

        response = self.client.post(
            reverse("core:settings"), {"scope": "tenant", "is_active": "on", "provider": "ollama"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(TenantAISetting.objects.get(tenant=self.tenant).provider, "ollama")

    def test_個人設定が禁止されていれば保存を拒む(self):
        TenantAISetting.objects.create(tenant=self.tenant, allow_personal_credentials=False)
        user = make_user(self.tenant, Role.PMO, suffix="-forbidden")
        self.client.force_login(user)

        self.client.post(
            reverse("core:settings"),
            {"scope": "user", "is_active": "on", "provider": "openai", "openai_api_key": RAW_KEY},
        )

        self.assertFalse(UserAISetting.objects.filter(user=user).exists())

    def test_設定変更を監査ログへ残す(self):
        from apps.audit.models import OperationLog

        user = make_user(self.tenant, Role.PMO, suffix="-audit")
        self.client.force_login(user)
        self.client.post(
            reverse("core:settings"),
            {"scope": "user", "is_active": "on", "provider": "openai", "openai_api_key": RAW_KEY},
        )

        log = OperationLog.objects.filter(action="AI設定の更新（個人）").first()

        self.assertIsNotNone(log)
        self.assertNotIn(RAW_KEY, log.detail + log.target)

    def test_接続確認はローカルハッシュなら常に成功する(self):
        user = make_user(self.tenant, Role.VIEWER, suffix="-verify")
        self.client.force_login(user)

        response = self.client.post(reverse("core:settings"), {"scope": "verify"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "外部 API を呼びません")


class EmbedderResolutionTests(TestCase):
    """個人設定が実際に AI 呼び出しへ届いているか。

    設定できても使われないなら、画面だけ増えて価値はゼロになる。
    """

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = make_user(self.tenant, Role.QUALITY_MANAGER, suffix="-embed")

    def test_キー未設定ならローカルハッシュへ退避する(self):
        from apps.core.services import ai_settings
        from apps.rag.services.embeddings import LocalHashEmbedder, get_embedder

        UserAISetting.objects.create(user=self.user, provider="openai")
        token = ai_settings.set_current_user(self.user)

        try:
            self.assertIsInstance(get_embedder(), LocalHashEmbedder)
        finally:
            ai_settings.reset_current_user(token)

    def test_個人のキーがあればそのプロバイダを使う(self):
        from apps.core.services import ai_settings
        from apps.rag.services.embeddings import OpenAIEmbedder, get_embedder

        UserAISetting.objects.create(user=self.user, provider="openai", openai_api_key=RAW_KEY)
        token = ai_settings.set_current_user(self.user)

        try:
            embedder = get_embedder()

            self.assertIsInstance(embedder, OpenAIEmbedder)
            self.assertEqual(embedder.config.openai_api_key, RAW_KEY)
        finally:
            ai_settings.reset_current_user(token)

    def test_文脈を戻せば他の利用者へ漏れない(self):
        from apps.core.services import ai_settings

        UserAISetting.objects.create(user=self.user, provider="openai", openai_api_key=RAW_KEY)
        token = ai_settings.set_current_user(self.user)
        ai_settings.reset_current_user(token)

        self.assertNotEqual(effective_config().openai_api_key, RAW_KEY)
