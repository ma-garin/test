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
