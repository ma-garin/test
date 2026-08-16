"""アップロードされたファイルの登録。

検証（`validation.validate_upload`）を通ったものだけ `Document` を作る。検証を
呼ばずに直接 `Document.objects.create()` する経路を増やさないため、画面からの
登録は必ずこの関数を通す。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.documents.models import Document, DocumentStatus
from apps.documents.services.validation import ValidationResult, validate_upload

#: 文書名が未入力のときにファイル名から採る最大長。`Document.title` は 300 文字。
TITLE_MAX_LENGTH = 300


@dataclass
class RegistrationResult:
    """登録の結果。失敗しても理由を必ず利用者へ返せる形にする。"""

    document: Document | None = None
    validation: ValidationResult | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_registered(self) -> bool:
        return self.document is not None


def register(
    *,
    uploaded_file,
    tenant,
    project=None,
    title: str = "",
    source_note: str = "",
    user=None,
) -> RegistrationResult:
    """ファイルを検証し、問題なければ文書として登録する。"""

    if tenant is None:
        return RegistrationResult(errors=["参照テナントが選択されていません。"])

    if uploaded_file is None:
        return RegistrationResult(errors=["ファイルが選択されていません。"])

    result = validate_upload(uploaded_file, tenant=tenant, project=project)

    if not result.is_valid:
        return RegistrationResult(validation=result, errors=result.errors)

    document = Document.objects.create(
        tenant=tenant,
        project=project,
        title=(title.strip() or uploaded_file.name)[:TITLE_MAX_LENGTH],
        file=uploaded_file,
        file_type=result.file_type,
        file_size=result.size,
        sha256=result.sha256,
        source_note=source_note.strip()[:300],
        uploaded_by=user,
        # 取込・インデックス構築は別ジョブ。登録直後は「RAG対象だが未インデックス」。
        status=DocumentStatus.ACTIVE,
    )

    return RegistrationResult(document=document, validation=result, warnings=result.warnings)
