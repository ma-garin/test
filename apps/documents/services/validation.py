"""文書登録前の検証。

再構築ブリーフ 6-1「文書を登録する前に、形式・必須項目・取込可否を検証する」に対応。
検証を通らないファイルは Document を作らず、理由を利用者へ返す。

ウイルススキャンはこのモジュールの責務ではない。保存先へ置く前段（アップロード
ハンドラまたはストレージ側）で実施する前提とし、ここは形式と整合性のみを見る。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from apps.documents.models import Document, FileType

#: 1 ファイルの上限。これを超えるものは分割して登録してもらう。
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024

EXTENSION_TO_FILE_TYPE = {
    ".pdf": FileType.PDF,
    ".xlsx": FileType.XLSX,
    ".xlsm": FileType.XLSM,
    ".xls": FileType.XLS,
    ".docx": FileType.DOCX,
    ".doc": FileType.DOC,
    ".pptx": FileType.PPTX,
}


@dataclass
class ValidationResult:
    is_valid: bool
    file_type: str = ""
    sha256: str = ""
    size: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def sha256_of(uploaded_file) -> str:
    digest = hashlib.sha256()

    for chunk in uploaded_file.chunks():
        digest.update(chunk)

    uploaded_file.seek(0)

    return digest.hexdigest()


def validate_upload(uploaded_file, *, tenant, project=None) -> ValidationResult:
    """アップロードされたファイルを検証する。

    重複（同一ハッシュ）はエラーではなく警告にする。同じ資料の版管理は
    利用者の判断に委ねる方が実務に合うため。
    """

    errors: list[str] = []
    warnings: list[str] = []

    name = getattr(uploaded_file, "name", "") or ""
    suffix = ("." + name.rsplit(".", 1)[-1]).lower() if "." in name else ""
    file_type = EXTENSION_TO_FILE_TYPE.get(suffix)

    if file_type is None:
        supported = ", ".join(sorted(EXTENSION_TO_FILE_TYPE))
        errors.append(f"対応していない形式です（{suffix or '拡張子なし'}）。対応形式: {supported}")

    size = getattr(uploaded_file, "size", 0) or 0

    if size == 0:
        errors.append("ファイルが空です。")
    elif size > MAX_FILE_SIZE_BYTES:
        limit_mb = MAX_FILE_SIZE_BYTES // (1024 * 1024)
        errors.append(f"サイズが上限（{limit_mb}MB）を超えています。")

    digest = ""

    if not errors:
        digest = sha256_of(uploaded_file)
        duplicate = Document.objects.filter(
            tenant=tenant,
            sha256=digest,
            deleted_at__isnull=True,
        ).first()

        if duplicate is not None:
            warnings.append(f"同じ内容の文書が登録済みです: {duplicate.title}")

    return ValidationResult(
        is_valid=not errors,
        file_type=file_type or "",
        sha256=digest,
        size=size,
        errors=errors,
        warnings=warnings,
    )
