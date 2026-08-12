"""Policy & Approval Authority（開発用fake実装）: capabilityの発行。

安全施策.md SC-06: capabilityの最大有効期限は10分。
署名はKMS/HSMではなく環境変数由来のHMAC鍵（開発用、本番運用不可）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings

from apps.pmo_authority.models import ExecutionCapability

#: 安全施策.md SC-06: capabilityの最大有効期限は10分。
MAX_TTL_SECONDS = 600

#: 署名対象に含めるフィールド（順序を固定し、正規化JSONにしてから署名する）。
_SIGNED_FIELDS = (
    "capability_id",
    "tenant_id",
    "project_id",
    "work_item_id",
    "plan_version",
    "policy_bundle_sha256",
    "requested_action",
    "resource_id",
    "payload_sha256",
    "evidence_bundle_sha256",
    "approved_by_subject_id",
    "required_role",
    "issued_at",
    "expires_at",
    "nonce",
    "idempotency_key",
)


class InvalidTtlError(ValueError):
    """ttl_seconds が安全施策.md SC-06 の上限(10分)を超えたことを表す。"""


class InsecureSigningKeyError(RuntimeError):
    """DEBUG=False（本番相当）なのに開発用デフォルト署名鍵のままであることを表す。"""


_DEV_ONLY_DEFAULT_SIGNING_KEY = "dev-only-insecure-default-signing-key"


def _signing_key() -> bytes:
    key = getattr(settings, "PMO_AUTHORITY_SIGNING_KEY", "") or _DEV_ONLY_DEFAULT_SIGNING_KEY
    # セキュリティレビュー指摘: 本番相当(DEBUG=False)で開発用デフォルト鍵のまま
    # 起動を続けると、署名が事実上無効化された状態で運用されてしまう。
    # 安全施策.md 11章がKMS/HSM管理者を人の決定事項としている以上、
    # 「気づかず本番運用される」ことを技術的に防ぐ。
    if key == _DEV_ONLY_DEFAULT_SIGNING_KEY and not getattr(settings, "DEBUG", True):
        raise InsecureSigningKeyError(
            "PMO_AUTHORITY_SIGNING_KEY が未設定のまま DEBUG=False で起動しています。"
            "この fake Authority は開発用鍵のままでは本番相当環境で使えません"
            "（安全施策.md 11章: KMS/HSM管理者が決まるまでは開発用途に限定してください）。"
        )
    return key.encode("utf-8")


def _canonical_payload(fields: dict) -> str:
    return json.dumps(fields, sort_keys=True, ensure_ascii=True, default=str)


def sign_payload(fields: dict) -> str:
    """フィールド辞書をHMAC-SHA256で署名する（開発用鍵、本番不可）。"""

    message = _canonical_payload({key: fields.get(key) for key in _SIGNED_FIELDS})
    return hmac.new(_signing_key(), message.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class CapabilityRequest:
    tenant: object
    project: object
    work_item_id: uuid.UUID
    plan_version: int
    policy_bundle_sha256: str
    requested_action: str
    resource_id: str
    payload_sha256: str
    evidence_bundle_sha256: str
    approved_by_subject_id: str
    required_role: str = ""


def issue_capability(
    request: CapabilityRequest, *, now: datetime, ttl_seconds: int = MAX_TTL_SECONDS
) -> ExecutionCapability:
    """署名付きcapabilityを発行する。DBへ一意なnonceで記録し、二重発行を防ぐ。"""

    if ttl_seconds <= 0 or ttl_seconds > MAX_TTL_SECONDS:
        raise InvalidTtlError(
            f"ttl_seconds は 1〜{MAX_TTL_SECONDS} 秒の範囲で指定してください（安全施策.md SC-06）。"
        )

    capability_id = uuid.uuid4()
    nonce = uuid.uuid4().hex
    expires_at = now + timedelta(seconds=ttl_seconds)

    fields = {
        "capability_id": str(capability_id),
        "tenant_id": str(request.tenant.id),
        "project_id": str(request.project.id),
        "work_item_id": str(request.work_item_id),
        "plan_version": request.plan_version,
        "policy_bundle_sha256": request.policy_bundle_sha256,
        "requested_action": request.requested_action,
        "resource_id": request.resource_id,
        "payload_sha256": request.payload_sha256,
        "evidence_bundle_sha256": request.evidence_bundle_sha256,
        "approved_by_subject_id": request.approved_by_subject_id,
        "required_role": request.required_role,
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "nonce": nonce,
        "idempotency_key": f"{request.work_item_id}:{request.plan_version}:{request.requested_action}",
    }
    signature = sign_payload(fields)

    return ExecutionCapability.objects.create(
        capability_id=capability_id,
        tenant=request.tenant,
        project=request.project,
        work_item_id=request.work_item_id,
        plan_version=request.plan_version,
        policy_bundle_sha256=request.policy_bundle_sha256,
        requested_action=request.requested_action,
        resource_id=request.resource_id,
        payload_sha256=request.payload_sha256,
        evidence_bundle_sha256=request.evidence_bundle_sha256,
        approved_by_subject_id=request.approved_by_subject_id,
        required_role=request.required_role,
        issued_at=now,
        expires_at=expires_at,
        nonce=nonce,
        idempotency_key=fields["idempotency_key"],
        signature=signature,
    )
