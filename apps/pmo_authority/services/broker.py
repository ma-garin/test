"""Execution Broker（開発用fake実装）: capabilityの検証と実行。

実際の外部システムへは一切接続しない。fake connector はダミー結果を
返すだけで、HTTP通信・外部プロセス呼び出しは行わない。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from django.db import transaction

from apps.pmo_authority.models import CapabilityStatus, ExecutionCapability
from apps.pmo_authority.services import audit, kill_switch
from apps.pmo_authority.services.authority import sign_payload


class CapabilityRejected(Exception):
    """capability検証に失敗し、fake connectorを一切呼ばずに拒否したことを表す。"""


def _capability_fields(capability: ExecutionCapability) -> dict:
    return {
        "capability_id": str(capability.capability_id),
        "tenant_id": str(capability.tenant_id),
        "project_id": str(capability.project_id),
        "work_item_id": str(capability.work_item_id),
        "plan_version": capability.plan_version,
        "policy_bundle_sha256": capability.policy_bundle_sha256,
        "requested_action": capability.requested_action,
        "resource_id": capability.resource_id,
        "payload_sha256": capability.payload_sha256,
        "evidence_bundle_sha256": capability.evidence_bundle_sha256,
        "approved_by_subject_id": capability.approved_by_subject_id,
        "required_role": capability.required_role,
        "issued_at": capability.issued_at.isoformat(),
        "expires_at": capability.expires_at.isoformat(),
        "nonce": capability.nonce,
        "idempotency_key": capability.idempotency_key,
    }


def _validation_error(
    capability: ExecutionCapability,
    *,
    connector: str,
    operation: str,
    current_payload_sha256: str,
    now: datetime,
    current_policy_bundle_sha256: str | None,
    expected_tenant_id: uuid.UUID,
    expected_project_id: uuid.UUID,
) -> str | None:
    """検証だけを行い、拒否理由（あれば）を返す。DB書き込みは一切しない。"""

    # 安全施策.md SC-08: kill switch は Authority と Broker の両方で毎回確認する。
    # 最も止めたいケース（緊急停止）を最初に評価する。
    kill_switch_reason = kill_switch.check_kill_switches(
        tenant_id=expected_tenant_id,
        project_id=expected_project_id,
        connector=connector,
        operation=operation,
    )
    if kill_switch_reason is not None:
        return kill_switch_reason

    expected_signature = sign_payload(_capability_fields(capability))
    if expected_signature != capability.signature:
        return "署名が一致しません。"

    if capability.status != CapabilityStatus.ISSUED:
        return (
            f"capabilityの状態がissuedではありません（status={capability.status}）。"
            "同一capabilityの再実行は許可しません。"
        )

    if capability.expires_at <= now:
        return "capabilityが失効しています。"

    if current_payload_sha256 != capability.payload_sha256:
        return "実行内容が承認時から変わっています。"

    if current_policy_bundle_sha256 is not None and current_policy_bundle_sha256 != capability.policy_bundle_sha256:
        return "policy bundleが承認時から差し替わっています。"

    # 安全施策.md SC-09 / SEC-07: capabilityのtenant/projectと、呼び出し元が
    # （DBから再解決した）実行対象の現在のtenant/projectが一致することを
    # 必ず再検証する。呼び出し元の値をそのまま信用しない（tenant Aの
    # capabilityをtenant Bのresourceと組み合わせる攻撃を検知する）。
    if str(capability.tenant_id) != str(expected_tenant_id):
        return "テナントがcapability発行時と一致しません（テナント越境の疑い）。"
    if str(capability.project_id) != str(expected_project_id):
        return "案件がcapability発行時と一致しません（境界越えの疑い）。"

    return None


def verify_and_execute(
    capability: ExecutionCapability,
    *,
    connector: str,
    operation: str,
    current_payload_sha256: str,
    now: datetime,
    correlation_id: uuid.UUID,
    expected_tenant_id: uuid.UUID,
    expected_project_id: uuid.UUID,
    current_policy_bundle_sha256: str | None = None,
) -> dict:
    """capabilityを検証し、通れば fake connector で「実行」する。

    検証: 署名の再計算一致、status=issued、期限内、payload_sha256が
    承認時から変わっていないこと、policy bundleが差し替わっていないこと
    （安全施策.md SC-06: policy bundleを差し替えると古いcapabilityは拒否される）、
    tenant/projectがcapability発行時と一致すること（SC-09/SEC-07）。
    いずれか失敗すれば fake connectorを呼ぶ前に CapabilityRejected で拒否する。

    同一capabilityへの同時リクエスト（レースコンディション）対策として、
    select_for_updateでロックを取得してから最新状態を判定する
    （セキュリティレビュー指摘: check-then-actでの二重通過を防ぐ）。

    監査記録（record_event）は、ロック区間のトランザクションが確定した後に
    行う。atomicブロック内で例外を投げると、そこで積んだ監査行自体も
    ロールバックされ「拒否された事実」が消えてしまうため
    （最初の実装で見つかったバグ）。
    """

    with transaction.atomic():
        capability = ExecutionCapability.objects.select_for_update().get(pk=capability.pk)
        rejection_reason = _validation_error(
            capability,
            connector=connector,
            operation=operation,
            current_payload_sha256=current_payload_sha256,
            now=now,
            current_policy_bundle_sha256=current_policy_bundle_sha256,
            expected_tenant_id=expected_tenant_id,
            expected_project_id=expected_project_id,
        )

        receipt: dict | None = None
        if rejection_reason is None:
            # ここまで検証を通った場合だけ、fake connector を呼ぶ（実際のHTTP通信は行わない）。
            external_id = f"fake-{uuid.uuid4()}"
            result_hash = hashlib.sha256(f"{connector}:{operation}:{capability.resource_id}".encode()).hexdigest()
            receipt = {"external_id": external_id, "result_hash": result_hash, "connector": connector}

            capability.status = CapabilityStatus.CONSUMED
            capability.save(update_fields=["status", "updated_at"])

    if rejection_reason is not None:
        audit.record_event(
            correlation_id=correlation_id,
            subject="broker",
            event_type="capability_rejected",
            result="rejected",
            detail={"capability_id": str(capability.capability_id), "reason": rejection_reason},
            now=now,
        )
        raise CapabilityRejected(rejection_reason)

    audit.record_event(
        correlation_id=correlation_id,
        subject="broker",
        event_type="capability_consumed",
        result="succeeded",
        detail={
            "capability_id": str(capability.capability_id),
            "connector": connector,
            "operation": operation,
            "external_id": receipt["external_id"],
            "result_hash": receipt["result_hash"],
        },
        now=now,
    )

    return receipt
