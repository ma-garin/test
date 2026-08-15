"""本番用設定。秘密値はすべて環境変数から読む。"""

from .base import *  # noqa: F403
from .base import env

DEBUG = False

SECRET_KEY = env("DJANGO_SECRET_KEY")

# 既定値を持たせない。`base.py` 側の `env()` には既定 ["localhost", "127.0.0.1"] があるため、
# 未設定のまま起動しても例外にならず、黙って通ってしまう。ここで明示的に必須にする。
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

# 本番で SQLite へ落ちないようにする。`base.py` の既定はファイル SQLite で、
# コンテナを作り直すたびにデータが消える壊れ方をする。
DATABASES = {"default": env.db_url("DATABASE_URL")}

# 画面のログインはメールアドレスだけで通り、未登録のアドレスは利用者を作って通す
# （`apps/accounts/backends.py`）。体験環境向けの割り切りであり、所有確認が無い。
# 本番でこれを有効にすると、アドレスを知っているだけで他人のテナントへ入れる。
# 所有確認（マジックリンク / SSO）を入れるまで、本番ではパスワード認証だけにする。
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 30)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# 共有端末で開きっぱなしのセッションが残らないようにする。
SESSION_COOKIE_AGE = env.int("DJANGO_SESSION_COOKIE_AGE", default=60 * 60 * 12)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
X_FRAME_OPTIONS = "DENY"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}
