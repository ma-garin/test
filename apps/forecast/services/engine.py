"""LDF-03: 決定論の着地予測エンジン。

機械学習を使わない。説明・修正・検証できる計算だけで、2日後・1週間後・最終期日の
着地を出す。`docs/改善に.md`「最小の予測ロジック（P0は決定論）」に対応する。

    タスク予測終了日 = max(
      タスク自身の確認済み終了見込み,
      max(先行タスク予測終了日 + 依存ラグ),
      必須の未解決ブロッカーの確認済み再試験完了見込み
    )
    マイルストーン予測日 = max(関連する必須タスク予測終了日)
    差分営業日 = マイルストーン予測日 - マイルストーン予定日

不変条件:
- 値が無いところを埋めない。埋められないものは `算定不能` と不足入力を返す。
- 候補（未確認）のリンクは日数計算に使わない。
- 暦日を営業日として扱わない。カレンダーが無ければ日数を出さない。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

from django.contrib.contenttypes.models import ContentType

from apps.forecast.models.estimates import ResolutionEstimate
from apps.forecast.models.snapshots import Confidence, Horizon, MissingInput
from apps.graph.models.schedule import MilestoneTaskLink, TaskDependency
from apps.graph.services.business_days import BusinessCalendar
from apps.projects.models import Defect, Milestone, WbsTask

#: 地平ごとの営業日数。「2日後」を暦日で数えると週末を跨いだ時点でずれる。
HORIZON_BUSINESS_DAYS = {Horizon.TWO_DAYS: 2, Horizon.ONE_WEEK: 5}

#: 予測の起点として使えないタスク状態。
FINISHED_STATUSES = (WbsTask.Status.DONE, WbsTask.Status.ARCHIVED)


@dataclass(frozen=True)
class TaskForecast:
    """1 タスクの予測終了日と、その根拠。"""

    task: WbsTask
    forecast_end: date | None
    #: `actual` / `confirmed_estimate` / `planned` / `predecessor` / `blocker`
    basis: str
    missing_inputs: tuple[str, ...] = ()
    #: この予測を押し出している先行タスク（クリティカルパスの説明に使う）。
    driven_by: tuple[str, ...] = ()

    @property
    def is_undeterminable(self) -> bool:
        return self.forecast_end is None

    @property
    def is_finished(self) -> bool:
        return self.task.status in FINISHED_STATUSES

    @property
    def basis_label(self) -> str:
        """予測終了日の根拠。内部キーを画面へ出さない。"""

        return {
            "actual": "実績",
            "confirmed_estimate": "確認済みの見込み",
            "planned": "計画終了日",
            "predecessor": "先行タスク待ち",
            "blocker": "ブロッカーの再試験待ち",
            "cycle": "循環依存",
            "unknown": "根拠なし",
        }.get(self.basis, self.basis)

    @property
    def missing_input_labels(self) -> tuple[str, ...]:
        labels = dict(MissingInput.choices)
        return tuple(labels.get(key, key) for key in self.missing_inputs)


@dataclass(frozen=True)
class TargetForecast:
    """マイルストーンまたは地平ごとの予測 1 件。"""

    target: object
    horizon: str
    baseline_date: date | None
    forecast_date: date | None
    variance_business_days: int | None
    confidence: str
    missing_inputs: tuple[str, ...]
    summary: str
    critical_path: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    @property
    def is_undeterminable(self) -> bool:
        return self.confidence == Confidence.UNKNOWN

    @property
    def confidence_label(self) -> str:
        """画面に内部キーを出さない。`unknown` ではなく `算定不能` と読ませる。"""

        return dict(Confidence.choices).get(self.confidence, self.confidence)

    @property
    def horizon_label(self) -> str:
        return dict(Horizon.choices).get(self.horizon, self.horizon)

    @property
    def missing_input_labels(self) -> tuple[str, ...]:
        labels = dict(MissingInput.choices)
        return tuple(labels.get(key, key) for key in self.missing_inputs)


@dataclass(frozen=True)
class ProjectForecast:
    """案件 1 件分の計算結果。画面・通知・報告はこれだけを読む。"""

    project: object
    as_of: date
    tasks: dict = field(default_factory=dict)
    targets: tuple[TargetForecast, ...] = ()
    calendar_missing: bool = False

    def for_horizon(self, horizon: str) -> tuple[TargetForecast, ...]:
        return tuple(item for item in self.targets if item.horizon == horizon)

    @property
    def undeterminable(self) -> tuple[TargetForecast, ...]:
        return tuple(item for item in self.targets if item.is_undeterminable)

    @property
    def delayed(self) -> tuple[TargetForecast, ...]:
        return tuple(
            item
            for item in self.targets
            if item.variance_business_days is not None and item.variance_business_days > 0
        )


def compute_project_forecast(project, as_of: date, freshness=None) -> ProjectForecast:
    """案件の全マイルストーンについて、3 時点の着地を計算する。

    `freshness` を渡すと、鮮度切れの情報源がある案件の確信度を下げる（AH-06）。
    渡さない場合は鮮度を評価しない。呼び出し側が明示的に選ぶ。
    """

    calendar = BusinessCalendar.for_project(project)
    tasks = list(WbsTask.objects.filter(project=project).exclude(status=WbsTask.Status.ARCHIVED))
    task_map = {task.pk: task for task in tasks}

    if calendar is None:
        # 暦日で代用しない。カレンダーが無い案件は日数を出さない。
        return ProjectForecast(
            project=project,
            as_of=as_of,
            tasks={},
            targets=_all_undeterminable(project, (MissingInput.NO_CALENDAR,)),
            calendar_missing=True,
        )

    dependencies = list(TaskDependency.objects.filter(project=project))
    blockers = _blockers_by_task(project)
    estimates = ResolutionEstimate.objects.for_targets([*tasks, *blockers.values()][:500])

    forecasts = _forecast_tasks(task_map, dependencies, blockers, estimates, calendar)
    targets = _forecast_targets(project, forecasts, calendar, as_of)
    targets = _apply_freshness(targets, freshness)

    return ProjectForecast(project=project, as_of=as_of, tasks=forecasts, targets=targets)


def _apply_freshness(targets, freshness) -> tuple[TargetForecast, ...]:
    """鮮度切れがあれば確信度を下げ、理由を不足入力へ足す。

    日付は消さない。古い情報でも計算根拠は残っているため、`算定不能` にするのではなく
    「信頼度が下がっている」ことを見せる。
    """

    if freshness is None or not getattr(freshness, "is_degraded", False):
        return targets

    return tuple(
        target
        if target.is_undeterminable
        else replace(
            target,
            confidence=Confidence.LOW,
            missing_inputs=_with_stale(target.missing_inputs),
        )
        for target in targets
    )


def _with_stale(missing_inputs: tuple[str, ...]) -> tuple[str, ...]:
    if MissingInput.STALE_SIGNAL in missing_inputs:
        return missing_inputs
    return (*missing_inputs, MissingInput.STALE_SIGNAL)


# ── タスクの予測 ─────────────────────────────────────────────


def _forecast_tasks(task_map, dependencies, blockers, estimates, calendar) -> dict:
    """依存グラフを前方向に計算する。循環があればその閉路を算定不能にする。"""

    predecessors: dict = {}
    for dependency in dependencies:
        predecessors.setdefault(dependency.successor_id, []).append(dependency)

    order, cyclic = _topological_order(task_map.keys(), dependencies)
    forecasts: dict = {}

    for task_id in order:
        task = task_map[task_id]
        forecasts[task_id] = _forecast_one(
            task, predecessors.get(task_id, ()), blockers.get(task_id), estimates, forecasts, calendar
        )

    for task_id in cyclic:
        forecasts[task_id] = TaskForecast(
            task=task_map[task_id],
            forecast_end=None,
            basis="cycle",
            missing_inputs=(MissingInput.CYCLIC_DEPENDENCY,),
        )
    return forecasts


def _forecast_one(task, incoming, blocker, estimates, forecasts, calendar) -> TaskForecast:
    base, basis, missing = _own_end(task, estimates)
    driven_by: list[str] = []

    for dependency in incoming:
        upstream = forecasts.get(dependency.predecessor_id)
        if upstream is None or upstream.forecast_end is None:
            # 先行が算定不能なら、後続の遅延を 0 と見なさない。
            return TaskForecast(
                task=task,
                forecast_end=None,
                basis="predecessor",
                missing_inputs=(MissingInput.NO_DEPENDENCY,),
                driven_by=(dependency.predecessor.wbs_code,),
            )
        candidate = calendar.add_business_days(
            upstream.forecast_end, max(dependency.lag_business_days, 0) + 1
        )
        if base is None or candidate > base:
            base, basis = candidate, "predecessor"
            driven_by = [dependency.predecessor.wbs_code]
        elif candidate == base:
            driven_by.append(dependency.predecessor.wbs_code)

    if blocker is not None:
        retest = estimates.get(_key_for(blocker))
        if retest is None:
            return TaskForecast(
                task=task,
                forecast_end=None,
                basis="blocker",
                missing_inputs=(MissingInput.UNRESOLVED_BLOCKER,),
                driven_by=(str(blocker),),
            )
        if base is None or retest.expected_date > base:
            base, basis = retest.expected_date, "blocker"
            driven_by = [str(blocker)]

    if base is None:
        return TaskForecast(task=task, forecast_end=None, basis=basis, missing_inputs=missing)

    return TaskForecast(
        task=task, forecast_end=base, basis=basis, driven_by=tuple(driven_by)
    )


def _own_end(task, estimates) -> tuple[date | None, str, tuple[str, ...]]:
    """タスク自身の終了見込み。完了は実績、進行中は確認済み見込み、未着手は計画。"""

    if task.status in FINISHED_STATUSES and task.actual_end:
        return task.actual_end, "actual", ()

    estimate = estimates.get(_key_for(task))
    if estimate is not None:
        return estimate.expected_date, "confirmed_estimate", ()

    if task.planned_end:
        return task.planned_end, "planned", ()

    return None, "unknown", (MissingInput.NO_PLANNED_END,)


def _topological_order(task_ids, dependencies) -> tuple[list, list]:
    """依存の計算順。閉路に含まれるタスクは別に返す。"""

    indegree = dict.fromkeys(task_ids, 0)
    successors: dict = {}
    for dependency in dependencies:
        if dependency.successor_id in indegree and dependency.predecessor_id in indegree:
            indegree[dependency.successor_id] += 1
            successors.setdefault(dependency.predecessor_id, []).append(dependency.successor_id)

    queue = [task_id for task_id, count in indegree.items() if count == 0]
    order: list = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for successor in successors.get(node, ()):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)

    cyclic = [task_id for task_id in indegree if task_id not in set(order)]
    return order, cyclic


def _blockers_by_task(project) -> dict:
    """確定した `blocks` 関連から、タスクを止めている未解決の不具合を引く。

    候補（未確認）のリンクは使わない。候補で納期を動かさないため。
    """

    from apps.graph.models.graph import WorkLink
    from apps.graph.ontology import RelationType

    defect_type = ContentType.objects.get_for_model(Defect)
    task_type = ContentType.objects.get_for_model(WbsTask)
    links = WorkLink.objects.confirmed().filter(
        project=project,
        relation_type=RelationType.BLOCKS,
        from_content_type=defect_type,
        to_content_type=task_type,
    )

    defect_ids = [link.from_object_id for link in links]
    unresolved = {
        defect.pk: defect
        for defect in Defect.objects.filter(pk__in=defect_ids).exclude(
            status=Defect.Status.CLOSED
        )
    }
    return {
        link.to_object_id: unresolved[link.from_object_id]
        for link in links
        if link.from_object_id in unresolved
    }


# ── マイルストーン・地平の予測 ───────────────────────────────


def _forecast_targets(project, forecasts, calendar, as_of) -> tuple[TargetForecast, ...]:
    milestones = list(Milestone.objects.filter(project=project))
    links = MilestoneTaskLink.objects.filter(
        milestone__in=milestones, is_required=True
    ).select_related("task")

    required: dict = {}
    confirmed_links: dict = {}
    for link in links:
        required.setdefault(link.milestone_id, []).append(link.task_id)
        confirmed_links.setdefault(link.milestone_id, []).append(link.is_confirmed)

    results: list[TargetForecast] = []
    for milestone in milestones:
        task_ids = required.get(milestone.pk, [])
        results.append(
            _forecast_milestone(
                milestone, task_ids, forecasts, calendar, confirmed_links.get(milestone.pk, [])
            )
        )
        results.extend(
            _forecast_horizon(milestone, task_ids, forecasts, calendar, as_of, horizon)
            for horizon in (Horizon.TWO_DAYS, Horizon.ONE_WEEK)
        )
    return tuple(results)


def _forecast_milestone(milestone, task_ids, forecasts, calendar, confirmations) -> TargetForecast:
    if not task_ids:
        return _undeterminable(
            milestone,
            Horizon.MILESTONE,
            (MissingInput.NO_MILESTONE_TASKS,),
            "必須WBSが紐付いていないため、着地日を算定できません。",
            baseline=milestone.planned_date,
        )

    relevant = [forecasts[task_id] for task_id in task_ids if task_id in forecasts]
    unknown = [item for item in relevant if item.is_undeterminable]
    if unknown or not relevant:
        missing = tuple({reason for item in unknown for reason in item.missing_inputs})
        return _undeterminable(
            milestone,
            Horizon.MILESTONE,
            missing or (MissingInput.NO_MILESTONE_TASKS,),
            f"必須WBS {len(unknown)} 件の終了見込みが立たないため算定不能です。",
            baseline=milestone.planned_date,
        )

    latest = max(relevant, key=lambda item: item.forecast_end)
    variance = calendar.business_days_between(milestone.planned_date, latest.forecast_end)
    confidence = _confidence_for(relevant, confirmations)

    return TargetForecast(
        target=milestone,
        horizon=Horizon.MILESTONE,
        baseline_date=milestone.planned_date,
        forecast_date=latest.forecast_end,
        variance_business_days=variance,
        confidence=confidence,
        missing_inputs=(),
        summary=_variance_summary(variance, latest.task.name),
        critical_path=(latest.task.wbs_code, *latest.driven_by),
    )


def _forecast_horizon(
    milestone, task_ids, forecasts, calendar, as_of, horizon
) -> TargetForecast:
    """2日後・1週間後。その時点で「次に何が終わる見込みか」を出す。

    単に今日へ日数を足した日付を出す表示にはしない。
    """

    horizon_date = calendar.add_business_days(as_of, HORIZON_BUSINESS_DAYS[horizon])
    relevant = [forecasts[task_id] for task_id in task_ids if task_id in forecasts]
    open_tasks = [item for item in relevant if not item.is_finished]

    if not open_tasks:
        return TargetForecast(
            target=milestone,
            horizon=horizon,
            baseline_date=horizon_date,
            forecast_date=horizon_date,
            variance_business_days=0,
            confidence=Confidence.HIGH,
            missing_inputs=(),
            summary="未完了の必須WBSはありません。",
        )

    unknown = [item for item in open_tasks if item.is_undeterminable]
    if unknown:
        missing = tuple({reason for item in unknown for reason in item.missing_inputs})
        return _undeterminable(
            milestone,
            horizon,
            missing,
            f"{unknown[0].task.name} の見込みが未確認のため、この時点の状況を算定できません。",
            baseline=horizon_date,
        )

    focus = min(open_tasks, key=lambda item: item.forecast_end)
    remaining = [item for item in open_tasks if item.forecast_end > horizon_date]
    variance = (
        calendar.business_days_between(focus.task.planned_end, focus.forecast_end)
        if focus.task.planned_end
        else None
    )

    return TargetForecast(
        target=milestone,
        horizon=horizon,
        baseline_date=focus.task.planned_end,
        forecast_date=focus.forecast_end,
        variance_business_days=variance,
        confidence=Confidence.MEDIUM if remaining else Confidence.HIGH,
        missing_inputs=(),
        summary=(
            f"{focus.task.name} の完了見込み。{horizon_date:%-m/%-d} 時点で "
            f"{len(remaining)} 件の必須WBSが残る見込みです。"
        ),
        blockers=tuple(item.task.wbs_code for item in remaining[:5]),
    )


def _confidence_for(relevant, confirmations) -> str:
    """確信度は AI の自己評価ではなく、入力品質から決める。"""

    all_links_confirmed = bool(confirmations) and all(confirmations)
    planned_only = [item for item in relevant if item.basis == "planned"]

    if all_links_confirmed and not planned_only:
        return Confidence.HIGH
    if all_links_confirmed or len(planned_only) < len(relevant):
        return Confidence.MEDIUM
    return Confidence.LOW


def _variance_summary(variance: int, task_name: str) -> str:
    if variance > 0:
        return f"{variance} 営業日の遅延見込み。最後に終わるのは「{task_name}」です。"
    if variance < 0:
        return f"{abs(variance)} 営業日の前倒し見込み。最後に終わるのは「{task_name}」です。"
    return f"予定どおりの着地見込み。最後に終わるのは「{task_name}」です。"


def _undeterminable(target, horizon, missing, summary, baseline=None) -> TargetForecast:
    return TargetForecast(
        target=target,
        horizon=horizon,
        baseline_date=baseline,
        forecast_date=None,
        variance_business_days=None,
        confidence=Confidence.UNKNOWN,
        missing_inputs=tuple(missing),
        summary=summary,
    )


def _all_undeterminable(project, missing) -> tuple[TargetForecast, ...]:
    return tuple(
        _undeterminable(
            milestone,
            horizon,
            missing,
            "勤務カレンダーが未設定のため、営業日での日数を算定できません。",
            baseline=milestone.planned_date,
        )
        for milestone in Milestone.objects.filter(project=project)
        for horizon in Horizon.values
    )


def _key_for(instance) -> tuple:
    return (ContentType.objects.get_for_model(instance).pk, instance.pk)
