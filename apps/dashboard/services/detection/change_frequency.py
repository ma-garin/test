"""#40 仕様変更頻度異常検知。

「直近の変更要求の発生ペースが、それ以前の平均を大きく超えたか」を見る。
件数そのものではなく 1 日あたりのペースで比べるのは、期間の長さが違うため。

観測数が少ないときは異常と判定しない。変更要求 2 件で「頻度異常」と言っても
根拠にならず、そういうアラートを出すと誰も読まなくなる。母数が
MIN_OBSERVATIONS 未満なら「判定不能」として、理由を残して見送る。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from apps.dashboard.models import Alert
from apps.dashboard.services.detection.findings import Finding, Skip, SkipReason
from apps.dashboard.services.detection.rules import rule_set
from apps.projects.models import ChangeRequest, Project

KIND = "change_frequency"


def detect(project: Project, *, now: datetime) -> tuple[list[Finding], list[Skip]]:
    conf = rule_set("CHANGE_FREQUENCY")
    window_days = int(conf["WINDOW_DAYS"])
    baseline_days = int(conf["BASELINE_DAYS"])
    min_observations = int(conf["MIN_OBSERVATIONS"])
    spike_ratio = float(conf["SPIKE_RATIO"])
    critical_ratio = float(conf["CRITICAL_SPIKE_RATIO"])
    baseline_span = max(baseline_days - window_days, 1)

    window_start = now - timedelta(days=window_days)
    baseline_start = now - timedelta(days=baseline_days)

    queryset = ChangeRequest.objects.filter(project=project)
    window_count = queryset.filter(created_at__gte=window_start).count()
    baseline_count = queryset.filter(
        created_at__gte=baseline_start, created_at__lt=window_start
    ).count()
    total = window_count + baseline_count

    if total < min_observations:
        return [], [
            Skip(
                project,
                KIND,
                SkipReason.INSUFFICIENT_DATA,
                f"直近{baseline_days}日の変更要求が {total}件 で、"
                f"判定に必要な {min_observations}件 に達しません。",
            )
        ]

    if baseline_count == 0:
        return [], [
            Skip(
                project,
                KIND,
                SkipReason.INSUFFICIENT_DATA,
                f"比較対象期間（{baseline_days}日前〜{window_days}日前）に変更要求がなく、"
                "平均と比較できません。",
            )
        ]

    window_rate = window_count / window_days
    baseline_rate = baseline_count / baseline_span
    ratio = window_rate / baseline_rate

    if ratio < spike_ratio:
        return [], [
            Skip(
                project,
                KIND,
                SkipReason.WITHIN_THRESHOLD,
                f"直近{window_days}日のペースは期間平均の {ratio:.2f}倍 で、"
                f"しきい値 {spike_ratio}倍 に達しません。",
            )
        ]

    reason = (
        f"直近{window_days}日の変更要求 {window_count}件"
        f"（{window_rate:.3f}件/日）は、それ以前{baseline_span}日の平均"
        f"{baseline_rate:.3f}件/日 の {ratio:.2f}倍 で、"
        f"しきい値 {spike_ratio}倍 を超えました。"
    )

    return [
        Finding(
            project=project,
            kind=KIND,
            dedupe_key=f"{KIND}:{project.code}",
            category=Alert.Category.CHANGE,
            severity=Alert.Severity.CRITICAL if ratio >= critical_ratio else Alert.Severity.WARNING,
            title=f"仕様変更の頻度異常: 直近{window_days}日で {window_count}件（平均の{ratio:.1f}倍）",
            detail=reason,
            evidence={
                "rule": KIND,
                "threshold": {
                    "spike_ratio": spike_ratio,
                    "min_observations": min_observations,
                    "window_days": window_days,
                    "baseline_days": baseline_days,
                },
                "observed": {
                    "window_count": window_count,
                    "baseline_count": baseline_count,
                    "window_rate_per_day": round(window_rate, 4),
                    "baseline_rate_per_day": round(baseline_rate, 4),
                    "ratio": round(ratio, 3),
                },
                "reason": reason,
            },
        )
    ], []
