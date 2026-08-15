"""秘密値の保存時暗号化。

API キーを利用者ごとに保存するようになると、環境変数に置いていたときと違い
「DB に平文で残る」経路ができる。バックアップ、ダンプ、管理画面、SQL クライアント
のいずれからも読めてしまうので、保存時に暗号化しておく。

鍵は `DJANGO_SECRET_KEY` から導出する。秘密の置き場所を増やさないための割り当てで、
鍵を回すと既存の暗号文は復号できなくなる（`decrypt()` は空文字を返す）。
その場合は利用者に再入力してもらう。復号できないことを例外にすると、
鍵を変えた瞬間に設定画面ごと開けなくなり、再入力すらできない。
"""

from __future__ import annotations

import base64
import hashlib
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

#: 暗号文であることの目印。平文と取り違えて二重暗号化しないために付ける。
PREFIX = "enc:v1:"

#: `config/settings/base.py` が持つ開発用の既定値。これで暗号化しても、
#: 鍵がリポジトリに書いてあるのと同じで、暗号化した意味が無い。
INSECURE_DEFAULT_KEY = "insecure-development-key-change-me"


def is_key_secure() -> bool:
    """API キーを保存してよい状態か（SECRET_KEY が既定値のままでないか）。"""

    return settings.SECRET_KEY != INSECURE_DEFAULT_KEY


def _fernet():
    """SECRET_KEY から導出した鍵で Fernet を作る。"""

    from cryptography.fernet import Fernet

    digest = hashlib.sha256(f"verirag.ai-credential:{settings.SECRET_KEY}".encode()).digest()

    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    """平文を暗号文へ。空文字はそのまま返す（未設定と暗号化済みの空を区別しない）。"""

    text = str(value or "")

    if not text:
        return ""

    if text.startswith(PREFIX):
        # すでに暗号化済み。フォームから戻ってきた値をそのまま渡された場合に備える。
        return text

    return PREFIX + _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    """暗号文を平文へ。復号できないときは空文字を返す。

    例外にしない理由はモジュール冒頭のとおり。「鍵が変わった」「DB を手で書き換えた」
    のどちらでも、画面は開けて再入力できる状態に保つ。
    """

    text = str(value or "")

    if not text:
        return ""

    if not text.startswith(PREFIX):
        # 暗号化前に保存された値。移行期のデータをそのまま読めるようにする。
        return text

    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(text[len(PREFIX) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        logger.warning("保存済みの認証情報を復号できませんでした。再入力が必要です。")

        return ""
