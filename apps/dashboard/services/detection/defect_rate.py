"""#41 バグ率異常検知。

見るのは 3 つ。件数の急増（発生率）、重大度の偏り、未クローズの滞留。
どれも母数が小さいと意味を持たないため、直近ウィンドウの不具合が
MIN_OBSERVATIONS 未満なら「判定不能」として見送る。

不具合の発生日は `detected_on` を正とし、未入力なら登録日時で代替する。
現場では検出日が後追いで入ることがあり、null を落とすと母数が過小になる。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.utils import timezone

from apps.dashboard.models import Alert
from apps.dashboard.services.detection.findings import Finding, Skip, SkipReason
from apps.dashboard.services.detection.rules import rule_set
from apps.projects.models import Defect, Project, Severity

KIND = "defect_rate"

#: 重大とみなす重大度。
SEVERE_LEVELS = (Severity.HIGH, Severity.CRITICAL)


def detect(project: Project, *, today: date) -> tuple[list[Finding], list[Skip]]:
    conf = rule_set("DEFECT_RATE")
    window_days = int(conf["WINDOW_DAYS"])
    baseline_days = int(conf["BASELINE_DAYS"])
    min_observations = int(conf["MIN_OBSERVATIONS"])
    severe_threshold = int(conf["SEVERE_RATIO_PERCENT"])
    open_threshold = int(conf["OPEN_RATIO_PERCENT"])
    spike_ratio = float(conf["SPIKE_RATIO"])
    baseline_span = max(baseline_days - window_days, 1)

    window_start = today - timedelta(days=window_days)
    baseline_start = today - timedelta(days=baseline_days)

    defects = list(Defect.objects.filter(project=project))
    window = [d for d in defects if window_start <= _occurred_on(d) <= today]
    baseline = [d for d in defects if baseline_start <= _occurred_on(d) < window_start]

    if len(window) < min_observations:
        return [], [
            Skip(
                project,
                KIND,
                SkipReason.INSUFFICIENT_DATA,
                f"直近{window_days}日の不具合が {len(window)}件 で、"
                f"判定に必要な {min_observations}件 に達しません。",
            )
        ]

    total = len(window)
    severe = [d for d in window if d.severity in SEVERE_LEVELS]
    unresolved = [d for d in window if d.status != Defect.Status.CLOSED]
    # total は min_observations 以上なので 0 除算にならない。
    severe_percent = len(severe) * 100 / total
    open_percent = len(unresolved) * 100 / total
    window_rate = total / window_days
    baseline_rate = len(baseline) / baseline_span
    ratio = (window_rate / baseline_rate) if baseline_rate else None

    breached: list[str] = []

    if severe_percent >= severe_threshold:
        breached.append(
            f"重大度「高・重大」が {len(severe)}/{total}件 = {severe_percent:.1f}%"
            f"（しきい値 {severe_threshold}%）"
        )

    if open_percent >= open_threshold:
        breached.append(
            f"未クローズが {len(unresolved)}/{total}件 = {open_percent:.1f}%"
            f"（しきい値 {open_threshold}%）"
        )

    if ratio is not None and ratio >= spike_ratio:
        breached.append(
            f"発生ペースが期間平均の {ratio:.2f}倍（しきい値 {spike_ratio}倍）"
        )

    if not breached:
        return [], [
            Skip(
                project,
                KIND,
                SkipReason.WITHIN_THRESHOLD,
                f"直近{window_days}日の不具合 {total}件。重大 {severe_percent:.1f}% ／ "
                f"未クローズ {open_percent:.1f}% で、いずれもしきい値内です。",
            )
        ]

    reason = f"直近{window_days}日の不具合 {total}件を評価: " + "／".join(breached)

    return [
        Finding(
            project=project,
            kind=KIND,
            dedupe_key=f"{KIND}:{project.code}",
            category=Alert.Category.QUALITY,
            severity=Alert.Severity.CRITICAL if len(breached) >= 2 else Alert.Severity.WARNING,
            title=f"バグ率の異常: 直近{window_days}日で {total}件（重大 {severe_percent:.0f}%）",
            detail=reason,
            evidence={
                "rule": KIND,
                "threshold": {
                    "min_observations": min_observations,
                    "severe_ratio_percent": severe_threshold,
                    "open_ratio_percent": open_threshold,
                    "spike_ratio": spike_ratio,
                    "window_days": window_days,
                },
                "observed": {
                    "window_count": total,
                    "severe_count": len(severe),
                    "severe_percent": round(severe_percent, 2),
                    "open_count": len(unresolved),
                    "open_percent": round(open_percent, 2),
                    "baseline_count": len(baseline),
                    "ratio": round(ratio, 3) if ratio is not None else None,
                },
                "breached": breached,
                "severity_breakdown": _breakdown(window),
                "reason": reason,
            },
        )
    ], []


def _occurred_on(defect: Defect) -> date:
    """発生日。検出日が未入力なら登録日で代替する。"""

    if defect.detected_on:
        return defect.detected_on

    created = defect.created_at

    if created is None:
        return timezone.localdate()

    return timezone.localtime(created).date() if timezone.is_aware(created) else created.date()


def _breakdown(defects: list[Defect]) -> dict[str, int]:
    """重大度ごとの内訳。合計だけでは「どこが偏ったか」を説明できない。"""

    counts: dict[str, int] = {}

    for defect in defects:
        counts[defect.severity] = counts.get(defect.severity, 0) + 1

    return counts
