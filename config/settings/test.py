"""テスト実行用設定。外部 API に触れないことを保証する。"""

from .base import *  # noqa: F403

DEBUG = False

# 既定はメモリ上の SQLite（速さを優先する）。
# 本番と同じ PostgreSQL で確かめたいときは DATABASE_URL を渡す。
# ここを固定にしていると、本番でだけ落ちる差異（並び順、型、
# 集約の挙動）をテストで拾えない。
DATABASES = {
    "default": env.db_url(  # noqa: F405
        "DATABASE_URL",
        default="sqlite:///:memory:",
    )
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
