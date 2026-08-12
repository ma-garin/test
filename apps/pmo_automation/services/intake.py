"""既存イベントから安全に PMO Work Item を作る intake。

`Alert` 等の既存の検知結果を受け取り、決定的な `dedupe_key` で重複を判定し、
Work Item を新規作成する／既存 Work Item に根拠(WorkLink)を束ねるだけを行う。
検知規則そのもの（何を異常とみなすか）はここでは変更しない。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.utils import timezone

from apps.dashboard.models import Alert
from apps.forecast.models import ForecastSnapshot
from apps.integrations.models import SyncJob
from apps.pmo_automation.models import PmoWorkItem, WorkKind, WorkLink
from apps.pmo_automation.services import planning
from apps.pmo_automation.services.rate_limit import check_intake_rate_limit

#: Alert.category から PMO Work Item の kind への割り当て。
#: P0 は種別を絞る方針（提案.md 4.2）に合わせ、いずれも detection_triage に寄せる。
_ALERT_CATEGORY_TO_KIND: dict[str, str] = {
    Alert.Category.SCHEDULE: WorkKind.DETECTION_TRIAGE,
    Alert.Category.QUALITY: WorkKind.DETECTION_TRIAGE,
    Alert.Category.RISK: WorkKind.DETECTION_TRIAGE,
    Alert.Category.CHANGE: WorkKind.DETECTION_TRIAGE,
    Alert.Category.RESOURCE: WorkKind.DETECTION_TRIAGE,
}


@dataclass(frozen=True)
class IntakeResult:
    """intake 1 回分の結果。dry_run のときも同じ形で返す。

    dry_run=True で新規作成が必要なケースは何も保存しないため work_item は
    None になる。既存 Work Item に統合するだけのケースは、dry_run でも
    その既存レコード（読み取りのみ）を返す。
    """

    work_item: PmoWorkItem | None
    created: bool
    dedupe_key: str


def build_dedupe_key(*, source_type: str, source_key: str) -> str:
    """source_type + source_key から決定的にキーを導く。"""

    return f"{source_type}:{source_key}"


def _integrate_or_link(
    *,
    tenant,
    project,
    kind: str,
    source_type: str,
    source_key: str,
    link_kwargs: dict,
    dry_run: bool,
) -> IntakeResult:
    """dedupe_key で既存 Work Item を探し、無ければ作る共通処理（FR-01: 重複排除）。

    既に同じ (tenant, dedupe_key) の有効 Work Item があれば、新規作成せず
    WorkLink だけを追加する。dry_run=True なら DB に一切書き込まない。
    """

    dedupe_key = build_dedupe_key(source_type=source_type, source_key=source_key)
    existing = PmoWorkItem.objects.filter(
        tenant=tenant, dedupe_key=dedupe_key, is_active=True
    ).first()

    if existing is not None:
        if not dry_run:
            WorkLink.objects.create(work_item=existing, **link_kwargs)

        # 既存レコードを返すこと自体は読み取りであり、dry-run不変条件
        # （DB書き込みをしない）には抵触しない。
        return IntakeResult(work_item=existing, created=False, dedupe_key=dedupe_key)

    # SEC-11: 新規Work Item作成のみをrate limit対象にする。既存への統合
    # （上のexisting分岐）はWork Item数を増やさないため対象外でよい。
    check_intake_rate_limit(tenant, now=timezone.now())

    if dry_run:
        return IntakeResult(work_item=None, created=True, dedupe_key=dedupe_key)

    work_item = PmoWorkItem.objects.create(
        tenant=tenant,
        project=project,
        kind=kind,
        source_type=source_type,
        source_key=source_key,
        dedupe_key=dedupe_key,
    )
    WorkLink.objects.create(work_item=work_item, **link_kwargs)

    return IntakeResult(work_item=work_item, created=True, dedupe_key=dedupe_key)


def intake_from_alert(alert: Alert, *, dry_run: bool = False) -> IntakeResult:
    """Alert を Work Item として取り込む。"""

    kind = _ALERT_CATEGORY_TO_KIND.get(alert.category, WorkKind.DETECTION_TRIAGE)

    return _integrate_or_link(
        tenant=alert.project.tenant,
        project=alert.project,
        kind=kind,
        source_type="alert",
        source_key=str(alert.pk),
        link_kwargs={"alert": alert},
        dry_run=dry_run,
    )


def intake_from_integration_job_failure(sync_job: SyncJob, *, dry_run: bool = False) -> IntakeResult:
    """同期失敗（SyncJob.status=failed/partial）を integration_recovery として取り込む。

    `Connection.project` が未設定（テナント全体の接続）の場合、どの案件の
    Work Item に帰属させるかは推測できないため、安全側に倒して対象外とする
    （project を勝手に割り当てない）。
    """

    if sync_job.status not in (SyncJob.Status.FAILED, SyncJob.Status.PARTIAL):
        raise ValueError("failed/partial以外のSyncJobはintake対象ではない。")

    connection = sync_job.connection
    if connection.project_id is None:
        raise ValueError(
            "テナント全体の接続（project未設定）の同期失敗は、"
            "帰属先の案件を推測できないためintake対象外。"
        )

    return _integrate_or_link(
        tenant=connection.tenant,
        project=connection.project,
        kind=WorkKind.INTEGRATION_RECOVERY,
        source_type="integration_job",
        source_key=str(sync_job.pk),
        link_kwargs={"integration_job": sync_job},
        dry_run=dry_run,
    )


def intake_from_forecast_undeterminable(snapshot: ForecastSnapshot, *, dry_run: bool = False) -> IntakeResult:
    """算定不能な `ForecastSnapshot` を `data_quality_repair` Work Item として取り込む（H-13）。

    値を推測して補完せず、`missing_inputs`（不足入力）と対象をそのまま
    担当者への確認依頼として渡す。予測日・営業日数のような算出値は
    一切保存しない（safety_assertion: 推測して保存しない）。

    `WorkLink` には ForecastSnapshot 用の参照フィールドが無いため、
    Alert/SyncJob 経路と異なり `WorkLink` は作らず、`EvidenceBundle.scope`
    に不足入力と対象を記録する（`_integrate_or_link` は使わない）。
    """

    if not snapshot.is_undeterminable:
        raise ValueError("算定不能でない ForecastSnapshot は intake 対象ではない。")
    if not snapshot.missing_inputs:
        raise ValueError("missing_inputs が空の ForecastSnapshot は処理できない。")

    project = snapshot.project
    tenant = project.tenant
    dedupe_key = build_dedupe_key(source_type="forecast_snapshot", source_key=str(snapshot.pk))

    existing = PmoWorkItem.objects.filter(
        tenant=tenant, dedupe_key=dedupe_key, is_active=True
    ).first()
    if existing is not None:
        return IntakeResult(work_item=existing, created=False, dedupe_key=dedupe_key)

    check_intake_rate_limit(tenant, now=timezone.now())

    if dry_run:
        return IntakeResult(work_item=None, created=True, dedupe_key=dedupe_key)

    missing_labels = snapshot.missing_input_labels()

    work_item = PmoWorkItem.objects.create(
        tenant=tenant,
        project=project,
        kind=WorkKind.DATA_QUALITY_REPAIR,
        source_type="forecast_snapshot",
        source_key=str(snapshot.pk),
        dedupe_key=dedupe_key,
        block_reason="算定不能: " + "、".join(missing_labels),
    )

    planning.record_evidence(
        work_item,
        source_type="forecast_snapshot",
        source_ref=str(snapshot.pk),
        scope={
            "tenant": tenant.code,
            "project": project.code,
            "missing_inputs": list(snapshot.missing_inputs),
            "target_type": snapshot.target_content_type.model,
            "target_id": str(snapshot.target_object_id),
            "target_label": str(snapshot.target) if snapshot.target is not None else "",
            "horizon": snapshot.horizon,
        },
        content_hash=hashlib.sha256(
            f"{snapshot.pk}:{sorted(snapshot.missing_inputs)}".encode()
        ).hexdigest(),
        captured_at=snapshot.as_of,
    )

    return IntakeResult(work_item=work_item, created=True, dedupe_key=dedupe_key)
