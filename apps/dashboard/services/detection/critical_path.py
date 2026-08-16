"""#5 クリティカルパス影響分析。

遅れているタスク単体では判断できない。PMO が知りたいのは
「その遅れが、後続のどの工程を、いくつ巻き込むか」である。

後続の定義は WbsTask が持つ関係のうち次の 2 つ。

- `related_tasks`（対称 M2M）… 依存関係として登録された関連タスク
- `parent`                  … まとめ工程。子が遅れれば親も遅れる

`related_tasks` は対称なので容易に閉路を作る。到達済み集合を持って
同じタスクを二度たどらないため、循環参照があっても停止する。
"""

from __future__ import annotations

from collections import deque
from datetime import date

from apps.dashboard.models import Alert
from apps.dashboard.services.detection.findings import Finding, Skip, SkipReason
from apps.dashboard.services.detection.rules import FINISHED_TASK_STATUSES, rule_set
from apps.projects.models import Project, WbsTask

KIND = "critical_path"


def detect(project: Project, *, today: date) -> tuple[list[Finding], list[Skip]]:
    """遅延タスクごとに後続への波及件数を数え、しきい値を超えたものを返す。"""

    conf = rule_set("CRITICAL_PATH")
    delay_days = int(conf["DELAY_DAYS"])
    max_depth = int(conf["MAX_DEPTH"])
    min_impacted = int(conf["MIN_IMPACTED_TASKS"])
    critical_impacted = int(conf["CRITICAL_IMPACTED_TASKS"])

    tasks = list(
        WbsTask.objects.filter(project=project)
        .select_related("parent")
        .prefetch_related("related_tasks")
    )

    if not tasks:
        return [], [Skip(project, KIND, SkipReason.INSUFFICIENT_DATA, "WBSタスクが登録されていません。")]

    by_pk = {task.pk: task for task in tasks}
    adjacency = _build_adjacency(tasks)

    findings: list[Finding] = []
    skips: list[Skip] = []

    for task in sorted(tasks, key=lambda t: t.wbs_code):
        delay = _delay_days(task, today)

        if delay is None or delay < delay_days:
            continue

        impacted = _impacted_tasks(
            task, adjacency=adjacency, by_pk=by_pk, max_depth=max_depth
        )

        if len(impacted) < min_impacted:
            skips.append(
                Skip(
                    project,
                    KIND,
                    SkipReason.WITHIN_THRESHOLD,
                    f"{task.wbs_code} は {delay}日 遅延しているが、"
                    f"未完了の後続が {len(impacted)}件（しきい値 {min_impacted}件）。",
                )
            )
            continue

        findings.append(_build_finding(project, task, impacted, delay=delay, conf=conf,
                                       critical_impacted=critical_impacted))

    if not findings and not skips:
        skips.append(
            Skip(project, KIND, SkipReason.WITHIN_THRESHOLD,
                 f"{delay_days}日以上遅延している未完了タスクはありません。")
        )

    return findings, skips


def _build_adjacency(tasks: list[WbsTask]) -> dict:
    """後続候補の隣接表。ここで 1 度だけ作り、探索中は追加クエリを出さない。"""

    adjacency: dict = {task.pk: set() for task in tasks}

    for task in tasks:
        for related in task.related_tasks.all():
            if related.pk in adjacency:
                adjacency[task.pk].add(related.pk)
                # 対称 M2M だが、片側しか読めていない場合に備えて逆向きも張る。
                adjacency[related.pk].add(task.pk)

        if task.parent_id and task.parent_id in adjacency:
            adjacency[task.pk].add(task.parent_id)

    return adjacency


def _impacted_tasks(source: WbsTask, *, adjacency: dict, by_pk: dict, max_depth: int) -> list[WbsTask]:
    """起点から到達できる未完了タスク。幅優先で深さを打ち切る。

    `visited` に入れてから広げるので、閉路があっても各タスクは 1 回しか処理しない。
    """

    visited = {source.pk}
    queue: deque = deque([(source.pk, 0)])
    impacted: list[WbsTask] = []

    while queue:
        current_pk, depth = queue.popleft()

        if depth >= max_depth:
            continue

        for next_pk in sorted(adjacency.get(current_pk, ()), key=lambda pk: str(pk)):
            if next_pk in visited:
                continue

            visited.add(next_pk)
            next_task = by_pk.get(next_pk)

            if next_task is None or next_task.status in FINISHED_TASK_STATUSES:
                continue

            impacted.append(next_task)
            queue.append((next_pk, depth + 1))

    return impacted


def _delay_days(task: WbsTask, today: date) -> int | None:
    """未完了タスクの遅延日数。完了済み・計画日なしは対象外。"""

    if task.planned_end is None or task.status in FINISHED_TASK_STATUSES:
        return None

    return (today - task.planned_end).days


def _build_finding(project, task, impacted, *, delay, conf, critical_impacted) -> Finding:
    is_critical = task.is_critical_path or len(impacted) >= critical_impacted
    reason = (
        f"{task.wbs_code} 「{task.name}」が計画終了日 {task.planned_end} を "
        f"{delay}日 超過（しきい値 {conf['DELAY_DAYS']}日）。"
        f"未完了の後続 {len(impacted)}件へ波及します。"
    )

    return Finding(
        project=project,
        kind=KIND,
        dedupe_key=f"{KIND}:{task.wbs_code}",
        category=Alert.Category.SCHEDULE,
        severity=Alert.Severity.CRITICAL if is_critical else Alert.Severity.WARNING,
        title=f"クリティカルパス遅延: {task.wbs_code} {task.name}（後続{len(impacted)}件に波及）",
        detail=reason,
        evidence={
            "rule": KIND,
            "threshold": {
                "delay_days": int(conf["DELAY_DAYS"]),
                "min_impacted_tasks": int(conf["MIN_IMPACTED_TASKS"]),
                "max_depth": int(conf["MAX_DEPTH"]),
            },
            "observed": {
                "delay_days": delay,
                "impacted_tasks": len(impacted),
                "is_critical_path": task.is_critical_path,
            },
            "source_task": {
                "id": str(task.pk),
                "wbs_code": task.wbs_code,
                "name": task.name,
                "planned_end": task.planned_end.isoformat() if task.planned_end else None,
                "status": task.status,
                "owner": task.owner,
            },
            "impacted_tasks": [
                {
                    "id": str(item.pk),
                    "wbs_code": item.wbs_code,
                    "name": item.name,
                    "planned_start": item.planned_start.isoformat() if item.planned_start else None,
                    "status": item.status,
                }
                for item in impacted
            ],
            "reason": reason,
        },
    )
