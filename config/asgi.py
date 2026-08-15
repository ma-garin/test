"""ASGI エントリポイント。"""

import os
from pathlib import Path

import environ
from django.core.asgi import get_asgi_application

# `.env` を先に読む。既定を本番へ倒しているため、先に読まないと
# 開発者が `.env` で local へ切り替えられない。
environ.Env.read_env(Path(__file__).resolve().parents[1] / ".env")

# 既定は本番。設定を渡し忘れた起動が DEBUG=True のまま立ち上がらないようにする。
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_asgi_application()
