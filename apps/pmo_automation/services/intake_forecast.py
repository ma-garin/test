"""予測の悪化（算定不能ではない）を PMO Work Item として取り込む。

`services.intake.intake_from_forecast_undeterminable` は算定不能
（confidence=unknown）を扱う。ここでは算定はできているが前回より悪化した
予測（variance_from_previous が正）を対象にする。

提案.md A-12: 「予測は単一値中心で、前回差分や不足データの影響が
作業につながらない。基準/悲観/楽観シナリオ、変化要因、データ充足度を
Work Item に接続する。期日変化の理由と不足を一つの承認パケットで示す。」
"""

from __future__ import annotations

import hashlib

from django.utils import timezone

from apps.forecast.models import ForecastSnapshot
from apps.pmo_automation.models import PmoWorkItem, WorkKind
from apps.pmo_automation.services import planning
from apps.pmo_automation.services.intake import IntakeResult, build_dedupe_key
from apps.pmo_automation.services.rate_limit import check_intake_rate_limit


def intake_from_forecast_regression(snapshot: ForecastSnapshot, *, dry_run: bool = False) -> IntakeResult:
    """予測が前回より悪化した場合に forecast_review Work Item を作る。

    算定不能自体は対象外（intake_from_forecast_undeterminable が別途処理する）。
    ここでは confidence 付きで既に確定している予測結果をそのまま記録するだけで、
    値を新たに算出・推測することはしない。
    """

    if snapshot.is_undeterminable:
        raise ValueError(
            "算定不能な ForecastSnapshot は intake_from_forecast_undeterminable の対象。"
        )

    variance = snapshot.variance_from_previous
    if variance is None or variance <= 0:
        raise ValueError(
            "前回より悪化していない（またはvariance算出不能な）予測はintake対象ではない。"
        )

    project = snapshot.project
    tenant = project.tenant
    dedupe_key = build_dedupe_key(
        source_type="forecast_snapshot_regression", source_key=str(snapshot.pk)
    )

    existing = PmoWorkItem.objects.filter(
        tenant=tenant, dedupe_key=dedupe_key, is_active=True
    ).first()
    if existing is not None:
        return IntakeResult(work_item=existing, created=False, dedupe_key=dedupe_key)

    check_intake_rate_limit(tenant, now=timezone.now())

    if dry_run:
        return IntakeResult(work_item=None, created=True, dedupe_key=dedupe_key)

    work_item = PmoWorkItem.objects.create(
        tenant=tenant,
        project=project,
        kind=WorkKind.FORECAST_REVIEW,
        source_type="forecast_snapshot_regression",
        source_key=str(snapshot.pk),
        dedupe_key=dedupe_key,
        block_reason=(
            f"予測が前回より{variance}営業日悪化（{snapshot.get_horizon_display()}）。"
        ),
    )

    planning.record_evidence(
        work_item,
        source_type="forecast_snapshot",
        source_ref=str(snapshot.pk),
        scope={
            "tenant": tenant.code,
            "project": project.code,
            "horizon": snapshot.horizon,
            "confidence": snapshot.confidence,
            "baseline_date": snapshot.baseline_date.isoformat() if snapshot.baseline_date else None,
            "forecast_date": snapshot.forecast_date.isoformat() if snapshot.forecast_date else None,
            "variance_business_days": snapshot.variance_business_days,
            "variance_from_previous": variance,
            "target_type": snapshot.target_content_type.model,
            "target_id": str(snapshot.target_object_id),
            "target_label": str(snapshot.target) if snapshot.target is not None else "",
        },
        content_hash=hashlib.sha256(
            f"{snapshot.pk}:{snapshot.forecast_date}:{snapshot.variance_business_days}".encode()
        ).hexdigest(),
        captured_at=snapshot.as_of,
    )

    return IntakeResult(work_item=work_item, created=True, dedupe_key=dedupe_key)
