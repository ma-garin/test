#!/usr/bin/env python
"""Django の管理コマンドエントリポイント。"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def main() -> None:
    # `.env` を先に読む。開発者は `.env` の DJANGO_SETTINGS_MODULE で local を選ぶ。
    # 既定を本番に倒しているので、先に読まないと開発設定へ切り替えられない。
    _load_env()

    # 既定は本番。設定を渡し忘れた起動が DEBUG=True・ALLOWED_HOSTS=["*"]・
    # 既定 SECRET_KEY のまま立ち上がると、例外画面に設定値が出てしまう。
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - 環境構築ミスの案内
        raise ImportError(
            "Django を import できません。仮想環境を有効化し、"
            "requirements/dev.txt をインストールしてください。"
        ) from exc

    execute_from_command_line(sys.argv)


def _load_env() -> None:
    try:
        import environ
    except ImportError:  # pragma: no cover - 依存未導入時は後段の案内へ委ねる
        return

    environ.Env.read_env(BASE_DIR / ".env")


if __name__ == "__main__":
    main()
