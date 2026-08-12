"""承認済み外部反映Outbox（PA-11、Authority/Broker結線）。

`docs/agent/pmo_autopilot_decisions.json` の D-04（最初に許可する承認付き
外部反映）は未決定のため、実際の外部システムには一切接続しない。ここで
検証するのは「承認 → capability発行 → Broker実行 → 受領証跡記録」という
配管（Outboxパターン）の技術的な形だけであり、connector は `fake` の
1種類だけを許可する（受入条件: 外部操作を一つに限定）。

D-04 が決まった後、`ALLOWED_CONNECTORS` を実 connector 名に差し替え、
`apps.pmo_authority.services.broker` 側の fake 実行を実接続へ置き換える
だけで本番化できるよう、インターフェースの形は変えない設計にする。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import transaction

from apps.pmo_automation.models import (
    ApprovalRequest,
    ApprovalStatus,
    EvidenceBundle,
    ExecutionAttempt,
    ExecutionOutcome,
    WorkStep,
    WorkStepState,
)
from apps.pmo_automation.services import policy
from apps.pmo_authority.services import authority, broker, policy_bundle

#: D-04（最初に許可する承認付き外部反映）が未決定の間は fake のみ許可する。
ALLOWED_CONNECTORS = frozenset({"fake"})


class DispatchRejected(Exception):
    """承認・失効・ロール等の再検証、または未許可connectorのため送信しなかったことを表す。"""


def _reject_and_hold(step: WorkStep, reason: str) -> None:
    """RUNNINGへ遷移させた後の検証失敗を扱う。RUNNINGのまま放置すると、
    以後の全リクエストが「送信処理中」として誤って拒否され続けてしまうため
    （デッドロック相当の不具合）、hold へ戻してから拒否する。
    """

    step.state = WorkStepState.HOLD
    step.result_summary = f"送信前の検証で拒否されました: {reason}"
    step.save(update_fields=["state", "result_summary", "updated_at"])
    raise DispatchRejected(reason)


def dispatch_approved_step(
    step: WorkStep,
    approval: ApprovalRequest,
    *,
    connector: str,
    operation: str,
    evidence_bundles: list[EvidenceBundle],
    expected_evidence_hash: str,
    actual_evidence_hash: str,
    actor_id,
    actor_subject_id: str,
    actor_role: str,
    required_role: str,
    now: datetime,
    correlation_id: UUID,
) -> ExecutionAttempt:
    """承認済み(approve automation_level)Stepを、Authority capability経由でfake送信する。

    画面表示時の判定結果を信用せず、実行直前に
    `policy.guard_awaiting_approval_to_executing` で承認の有効性
    （未失効・plan版一致・根拠鮮度・ロール・自己承認でないこと）を
    再検証してから進める（FR-02、forbidden_actions「承認なし送信」対応）。

    冪等性: 既に SUCCEEDED の Step は再送しない（executor.py(PA-06)と同じ規約）。
    同時に2つのリクエストが同じStepをdispatchしようとするレースコンディション
    （セキュリティレビュー指摘: 逐次リトライの冪等性チェックだけでは、
    check-then-actの隙間で両方が通過し二重送信しうる）に対しては、
    select_for_update + RUNNING状態への即時コミットで直列化する
    （apps.pmo_authority.services.broker と同じ設計方針）。

    既知の限界（レビュー指摘）: 開発環境の既定DB(SQLite)は `SELECT ... FOR UPDATE`
    を行レベルロックとして実装しておらず、Djangoはこれを黙って無視する。
    SQLiteは書き込みトランザクションをファイルロックで事実上シリアライズする
    ため偶然動作しうるが、意図した行ロックの保証はない。本番でPostgreSQL等
    行ロック対応DBを使う場合は正しく機能する設計だが、SQLiteでの並行実行
    耐性は未検証のまま残る。
    """

    with transaction.atomic():
        step = WorkStep.objects.select_for_update().get(pk=step.pk)

        if step.state == WorkStepState.SUCCEEDED:
            existing = step.attempts.filter(outcome=ExecutionOutcome.SUCCEEDED).order_by("-created_at").first()
            if existing is not None:
                return existing
            raise DispatchRejected(
                "Stepはsucceeded状態ですが対応するExecutionAttemptが見つかりません（不整合のため安全側で拒否）。"
            )

        if step.state == WorkStepState.RUNNING:
            raise DispatchRejected("Stepは既に送信処理中です（同時実行による重複を防止）。")

        step.state = WorkStepState.RUNNING
        step.save(update_fields=["state", "updated_at"])

    # ここでロックは解放される（RUNNINGへの遷移がコミット済み）。以降の
    # capability発行・Broker呼出・監査記録は、ロック区間の外で行う
    # （broker.py側の「監査記録はatomicブロックの外」という設計と揃える）。

    if connector not in ALLOWED_CONNECTORS:
        _reject_and_hold(
            step,
            f"許可されていないconnectorです: {connector}"
            f"（D-04未決定のため {sorted(ALLOWED_CONNECTORS)} のみ許可）。",
        )

    guard = policy.guard_awaiting_approval_to_executing(
        approval=approval,
        plan_version=step.plan.version,
        evidence_bundles=evidence_bundles,
        expected_evidence_hash=expected_evidence_hash,
        actual_evidence_hash=actual_evidence_hash,
        actor_id=actor_id,
        actor_role=actor_role,
        required_role=required_role,
        now=now,
    )
    if not guard.passed:
        _reject_and_hold(step, guard.reason)

    if approval.status != ApprovalStatus.APPROVED:
        _reject_and_hold(step, f"承認済み(approved)ではありません（status={approval.status}）。")

    started_at = now
    # RUNNINGへ遷移させて以降は、issue_capability/verify_and_executeの
    # どちらで何が起きても必ずholdへ決着させる（セキュリティレビュー指摘対応:
    # issue_capability呼び出しがtry/exceptで保護されておらず、そこで例外が
    # 起きるとStepがRUNNINGのまま永久に残るバグがあった）。
    try:
        work_item = step.plan.work_item
        # 安全施策.md SC-01/SEC-01: issue_capabilityは署名済みpolicy bundle
        # しか受理しない。D-04未決定の間は開発用の既定bundleを使う
        # （無ければ自動でpublishされる）。
        dev_bundle = policy_bundle.get_or_create_dev_default_bundle()
        capability = authority.issue_capability(
            authority.CapabilityRequest(
                tenant=work_item.tenant,
                project=work_item.project,
                work_item_id=work_item.id,
                plan_version=step.plan.version,
                policy_bundle_sha256=dev_bundle.content_sha256,
                requested_action=approval.requested_action,
                resource_id=step.kind,
                payload_sha256=actual_evidence_hash,
                evidence_bundle_sha256=actual_evidence_hash,
                approved_by_subject_id=actor_subject_id,
                required_role=required_role,
            ),
            now=now,
        )
        receipt = broker.verify_and_execute(
            capability,
            connector=connector,
            operation=operation,
            current_payload_sha256=actual_evidence_hash,
            expected_tenant_id=work_item.tenant_id,
            expected_project_id=work_item.project_id,
            now=now,
            correlation_id=correlation_id,
        )
    except broker.CapabilityRejected as error:
        step.state = WorkStepState.HOLD
        step.result_summary = f"外部送信がBrokerに拒否されました: {error}"
        step.save(update_fields=["state", "result_summary", "updated_at"])

        return ExecutionAttempt.objects.create(
            step=step,
            started_at=started_at,
            ended_at=now,
            outcome=ExecutionOutcome.FAILED,
            failure_category="policy",
            safe_summary=str(error),
        )
    except Exception as error:
        # 予期しない例外（capability発行のバリデーションエラー等）でも
        # RUNNINGのまま残さない。安全側でholdへ倒し、原因は再送出して
        # 呼び出し元・ログに伝える。
        step.state = WorkStepState.HOLD
        step.result_summary = f"送信処理中に予期しないエラーが発生しました: {error}"
        step.save(update_fields=["state", "result_summary", "updated_at"])
        raise

    step.state = WorkStepState.SUCCEEDED
    step.attempt_count += 1
    step.result_summary = "外部送信成功（fake connector）。"
    step.save(update_fields=["state", "attempt_count", "result_summary", "updated_at"])

    return ExecutionAttempt.objects.create(
        step=step,
        started_at=started_at,
        ended_at=now,
        outcome=ExecutionOutcome.SUCCEEDED,
        external_receipt=receipt,
    )
