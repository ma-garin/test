"""テスト実行用設定。外部 API に触れないことを保証する。"""

from .base import *  # noqa: F403

DEBUG = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# テストでは必ずローカル Embedding を使い、OpenAI / Ollama を呼ばない。
AI_PROVIDER = "local_hash"

# pmo_authority: DEBUG=False では開発用デフォルト鍵の使用を拒否する設計のため、
# テスト専用の鍵を明示する（本番鍵ではない）。
PMO_AUTHORITY_SIGNING_KEY = "test-only-signing-key-not-for-production"
