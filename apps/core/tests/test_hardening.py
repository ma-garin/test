"""設定まわりの守り。

いずれも「設定を1つ間違えると本番で穴になる」種類のもの。テストで固定しておかないと、
次に誰かが設定ファイルを整理したときに黙って戻る。
"""

from __future__ import annotations

import importlib
import os
from unittest import mock

from django.test import TestCase, override_settings

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.pagination import paginate
from apps.core.services.ai_settings import AIConfig, is_allowed_ollama_url, verify_connection
from apps.core.services.secrets import INSECURE_DEFAULT_KEY, is_key_secure


#: 本番設定は必須の環境変数を要求する（未設定なら起動しないのが正しい）。
#: テストから読むためだけの最小の値。
PRODUCTION_ENV = {
    "DJANGO_SECRET_KEY": "test-only-production-secret-key-value",
    "DJANGO_ALLOWED_HOSTS": "example.com",
    "DATABASE_URL": "sqlite:////tmp/production-smoke.sqlite3",
}


def load_production_settings():
    """本番設定モジュールを読み込む。必須の環境変数を与えたうえで読み直す。"""

    with mock.patch.dict(os.environ, PRODUCTION_ENV):
        return importlib.reload(importlib.import_module("config.settings.production"))


class ProductionSettingsTests(TestCase):
    """本番設定の smoke test。

    画面のログインはメールアドレスだけで通り、未登録アドレスは利用者を作って通す。
    体験環境向けの割り切りなので、本番設定で外れていることを固定する。
    """

    def test_必須の環境変数が無ければ起動させない(self):
        """未設定のまま黙って SQLite や localhost へ落ちないこと。"""

        from django.core.exceptions import ImproperlyConfigured

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ImproperlyConfigured):
                importlib.reload(importlib.import_module("config.settings.production"))

    def test_本番ではメールのみ認証を使わない(self):
        module = load_production_settings()

        self.assertEqual(
            module.AUTHENTICATION_BACKENDS,
            ["django.contrib.auth.backends.ModelBackend"],
        )

    def test_本番はDEBUGを落としクッキーを保護する(self):
        module = load_production_settings()

        self.assertFalse(module.DEBUG)
        self.assertTrue(module.SESSION_COOKIE_SECURE)
        self.assertTrue(module.SESSION_COOKIE_HTTPONLY)
        self.assertTrue(module.CSRF_COOKIE_SECURE)
        self.assertEqual(module.X_FRAME_OPTIONS, "DENY")

    def test_エントリポイントの既定は本番設定(self):
        """設定を渡し忘れた起動が DEBUG=True で立ち上がらないこと。"""

        from pathlib import Path

        root = Path(__file__).resolve().parents[3]

        for name in ("manage.py", "config/wsgi.py", "config/asgi.py"):
            with self.subTest(entry=name):
                source = (root / name).read_text(encoding="utf-8")

                self.assertIn('setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")', source)


class SecretKeyTests(TestCase):
    @override_settings(SECRET_KEY=INSECURE_DEFAULT_KEY)
    def test_既定の鍵のままなら安全でないと判定する(self):
        self.assertFalse(is_key_secure())

    def test_鍵を差し替えれば安全と判定する(self):
        self.assertTrue(is_key_secure())


class OllamaAllowlistTests(TestCase):
    """接続確認は「利用者の指定した宛先へサーバから通信する」操作。

    宛先を自由にすると内部ネットワークの探索に使える。到達可否とエラー本文が
    画面へ返るので、応答の差がそのまま情報になる。
    """

    def test_ローカルは既定で許可する(self):
        self.assertTrue(is_allowed_ollama_url("http://localhost:11434"))
        self.assertTrue(is_allowed_ollama_url("http://127.0.0.1:11434"))

    def test_内部アドレスや外部は許可しない(self):
        for url in (
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5:8080",
            "https://example.com",
            "file:///etc/passwd",
            "",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_allowed_ollama_url(url))

    @override_settings(AI_OLLAMA_ALLOWED_HOSTS=["ollama.internal"])
    def test_管理者が許可した宛先は通す(self):
        self.assertTrue(is_allowed_ollama_url("http://ollama.internal:11434"))

    def test_許可されない宛先は通信せずに拒否する(self):
        config = AIConfig(provider="ollama", ollama_base_url="http://169.254.169.254/")

        result = verify_connection(config)

        self.assertFalse(result.ok)
        self.assertIn("許可されていません", result.message)


class PaginationOrderingTests(TestCase):
    """並び順の無い一覧をページングすると、行が重複したり消えたりする。"""

    def test_並び順が無ければ主キーで安定させる(self):
        tenant = Tenant.objects.create(code="acme", name="ACME")

        for index in range(3):
            User.objects.create_user(
                username=f"u{index}",
                email=f"u{index}@example.com",
                password="x",
                tenant=tenant,
                role=Role.VIEWER,
            )

        request = self.client.request().wsgi_request
        page = paginate(User.objects.all(), request, per_page=2)

        self.assertTrue(page.object_list.ordered)
        self.assertEqual(len(page.object_list), 2)


class TenantSelectionTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

    def test_不正なテナントIDで500にしない(self):
        from django.urls import reverse

        response = self.client.post(reverse("accounts:select_tenant"), {"tenant": "not-a-uuid"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "選択できません")

    def test_不正な案件IDで500にしない(self):
        from django.urls import reverse

        response = self.client.post(reverse("accounts:select_project"), {"project": "not-a-uuid"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "選択できません")

    def test_無効化したテナントには入れない(self):
        from django.urls import reverse

        self.tenant.is_active = False
        self.tenant.save(update_fields=["is_active"])

        response = self.client.get(reverse("dashboard:control"))

        self.assertIsNone(response.wsgi_request.tenant)
