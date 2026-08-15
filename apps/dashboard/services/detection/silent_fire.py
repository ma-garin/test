"""#7 サイレント炎上の先行検知。

表面化した課題は誰かが見ている。危ないのは「誰も騒いでいないのに止まっている」
タスクである。単独の兆候では誤検知になるため、複数の兆候が重なったときだけ検知する。

兆候は WbsTask から取れる次の 4 つ。

1. 更新が止まった          … `updated_at` が STALE_UPDATE_DAYS 以上前
2. ボールが動かない        … `ball_holder` が居るのに長期間更新なし
3. 期限超過が放置されている … 計画終了日を過ぎているのにフォロー状態が「フォロー不要」
4. 進捗が伸びていない      … 計画期間の半分を過ぎたのに進捗が LOW_PROGRESS_PERCENT 未満

「期限が繰り返し延びた」は本来 planned_end の変更履歴で測るべきだが、
WbsTask は履歴を持たない。ここでは 3 と 4（期限超過の放置と進捗の停滞）を
代理指標として扱う。履歴テーブルを持たせたら差し替えること。
"""

from __future__ import annotations

from datetime import date

from django.utils import timezone

from apps.dashboard.models import Alert
from apps.dashboard.services.detection.findings import Finding, Skip, SkipReason
from apps.dashboard.services.detection.rules import FINISHED_TASK_STATUSES, rule_set
from apps.projects.models import Project, WbsTask

KIND = "silent_fire"


def detect(project: Project, *, today: date) -> tuple[list[Finding], list[Skip]]:
    conf = rule_set("SILENT_FIRE")
    min_signals = int(conf["MIN_SIGNALS"])
    critical_signals = int(conf["CRITICAL_SIGNALS"])

    tasks = list(
        WbsTask.objects.filter(project=project).exclude(status__in=FINISHED_TASK_STATUSES)
    )

    if not tasks:
        return [], [Skip(project, KIND, SkipReason.INSUFFICIENT_DATA, "未完了のWBSタスクがありません。")]

    findings: list[Finding] = []
    skips: list[Skip] = []

    for task in sorted(tasks, key=lambda t: t.wbs_code):
        signals = _signals(task, today=today, conf=conf)

        if len(signals) < min_signals:
            continue

        findings.append(
            _build_finding(project, task, signals, conf=conf, critical_signals=critical_signals)
        )

    if not findings:
        skips.append(
            Skip(project, KIND, SkipReason.WITHIN_THRESHOLD,
                 f"兆候が {min_signals}件以上重なった未完了タスクはありません（対象 {len(tasks)}件）。")
        )

    return findings, skips


def _stale_days(task: WbsTask, today: date) -> int:
    """最終更新からの経過日数。更新が無い＝誰も触っていない、の代理指標。"""

    updated = task.updated_at

    if updated is None:
        return 0

    updated_date = timezone.localtime(updated).date() if timezone.is_aware(updated) else updated.date()

    return max((today - updated_date).days, 0)


def _signals(task: WbsTask, *, today: date, conf: dict) -> list[dict]:
    """重なった兆候の一覧。1 件ずつ「どの数字がしきい値をどう超えたか」を持つ。"""

    signals: list[dict] = []
    stale = _stale_days(task, today)
    stale_threshold = int(conf["STALE_UPDATE_DAYS"])
    ball_threshold = int(conf["SAME_BALL_HOLDER_DAYS"])
    low_progress = int(conf["LOW_PROGRESS_PERCENT"])

    if stale >= stale_threshold:
        signals.append({
            "key": "stale_update",
            "label": "更新が止まっている",
            "observed": stale,
            "threshold": stale_threshold,
            "unit": "日",
        })

    if task.ball_holder and stale >= ball_threshold:
        signals.append({
            "key": "same_ball_holder",
            "label": f"ボール保持者「{task.ball_holder}」のまま動いていない",
            "observed": stale,
            "threshold": ball_threshold,
            "unit": "日",
        })

    if task.planned_end and task.planned_end < today and task.follow_up_state == WbsTask.FollowUpState.NONE:
        signals.append({
            "key": "overdue_unflagged",
            "label": "期限超過なのにPMOフォローが立っていない",
            "observed": (today - task.planned_end).days,
            "threshold": 0,
            "unit": "日超過",
        })

    progress = int(task.progress_percent or 0)

    if progress < low_progress and _past_half_of_plan(task, today):
        signals.append({
            "key": "progress_stalled",
            "label": "計画期間の半分を過ぎたのに進捗が伸びていない",
            "observed": progress,
            "threshold": low_progress,
            "unit": "%",
        })

    return signals


def _past_half_of_plan(task: WbsTask, today: date) -> bool:
    """計画期間の折り返しを過ぎたか。計画日が無ければ判定しない。"""

    if task.planned_start is None or task.planned_end is None:
        return False

    span = (task.planned_end - task.planned_start).days

    if span <= 0:
        return task.planned_end <= today

    return (today - task.planned_start).days * 2 >= span


def _build_finding(project, task, signals, *, conf, critical_signals) -> Finding:
    summary = "／".join(
        f"{signal['label']}（{signal['observed']}{signal['unit']} ＞ "
        f"しきい値 {signal['threshold']}{signal['unit']}）"
        for signal in signals
    )
    reason = f"{task.wbs_code} 「{task.name}」に兆候が {len(signals)}件 重なっています: {summary}"

    return Finding(
        project=project,
        kind=KIND,
        dedupe_key=f"{KIND}:{task.wbs_code}",
        category=Alert.Category.RISK,
        severity=Alert.Severity.CRITICAL if len(signals) >= critical_signals else Alert.Severity.WARNING,
        title=f"サイレント炎上の予兆: {task.wbs_code} {task.name}（兆候{len(signals)}件）",
        detail=reason,
        evidence={
            "rule": KIND,
            "threshold": {
                "min_signals": int(conf["MIN_SIGNALS"]),
                "stale_update_days": int(conf["STALE_UPDATE_DAYS"]),
                "same_ball_holder_days": int(conf["SAME_BALL_HOLDER_DAYS"]),
                "low_progress_percent": int(conf["LOW_PROGRESS_PERCENT"]),
            },
            "observed": {"signal_count": len(signals)},
            "signals": signals,
            "source_task": {
                "id": str(task.pk),
                "wbs_code": task.wbs_code,
                "name": task.name,
                "owner": task.owner,
                "ball_holder": task.ball_holder,
                "follow_up_state": task.follow_up_state,
            },
            "reason": reason,
        },
    )
