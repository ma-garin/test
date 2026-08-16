#!/usr/bin/env python
"""Django の管理コマンドエントリポイント。"""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - 環境構築ミスの案内
        raise ImportError(
            "Django を import できません。仮想環境を有効化し、"
            "requirements/dev.txt をインストールしてください。"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
