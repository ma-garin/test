"""日本語・英数字混在テキストのトークン化。

旧 `pmo_agent/retrieval.py` の `tokenize_text()` をそのまま移植したもの。
形態素解析器を持たない環境でも語彙検索が成立するよう、日本語は文字 bi-gram へ
展開する。既存インデックスとの互換を保つため、挙動を変えないこと。
"""

from __future__ import annotations

import hashlib
import re

TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[ぁ-んァ-ン一-龥ー]{2,}", re.IGNORECASE)
_JAPANESE_PATTERN = re.compile(r"[ぁ-んァ-ン一-龥]")


def tokenize(text: str) -> list[str]:
    """検索用トークン列を返す。

    英数字はそのまま、日本語の 3 文字以上の連なりは元の語に加えて bi-gram も返す。
    """

    normalized = str(text or "").casefold()
    tokens: list[str] = []

    for match in TOKEN_PATTERN.findall(normalized):
        token = match.strip()

        if not token:
            continue

        tokens.append(token)

        if _JAPANESE_PATTERN.search(token) and len(token) >= 3:
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))

    return tokens


def chunk_key(document_id, page_number: int, position: int, text: str) -> str:
    """再構築しても同じ値になるチャンク ID。

    旧 `chunk_identifier()` と同じ考え方だが、文書の絶対パスではなく文書 ID を使う。
    パスに依存しないため、環境を移しても ID が変わらない。
    """

    digest = hashlib.sha1(
        f"{document_id}|{page_number}|{position}|{text[:240]}".encode("utf-8", errors="ignore")
    ).hexdigest()[:16]

    return f"chk_{digest}"
