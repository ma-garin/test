"""入力標準ルールの運用支援（要件 #47）。

予兆検知も進捗予測も、入力されたデータしか見られない。担当が空・期限が空・
Blocked なのに次アクションが無い、という状態のタスクが混じっていると、
検知は「異常なし」と言い、そのまま炎上する。**入力の穴は検知の穴になる。**

そこで「PMO が口頭で言っている運用ルール」を明文化して、遵守状況を数える。
ルールは増える前提なので、判定を 1 か所（`RULES`）に並べて持つ。

意図的に AI を使わない。ルール違反は数えれば分かることで、推論する余地が無い。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import QuerySet
from django.utils import timezone

from apps.projects.models import Project, WbsTask

#: 進行中タスクの更新間隔。週次定例（金曜）で棚卸しする運用を前提に 7 日とする。
STALE_AFTER_DAYS = 7

#: 一覧に出す違反タスクの上限。全部出すと画面が読めなくなる。
MAX_SAMPLES = 10

#: 遵守率がこれを下回るルールを「要改善」とする。
WARN_PERCENT = 90


@dataclass(frozen=True)
class RuleSpec:
    """入力ルール 1 件。

    `applies` が母数の条件、`violates` が違反の条件。母数を分けて持つのは、
    「対象 0 件」と「全件違反」を区別するため。
    """

    key: str
    label: str
    guidance: str
    applies: object
    violates: object


def _is_active(task: WbsTask) -> bool:
    return task.status not in (WbsTask.Status.DONE, WbsTask.Status.ARCHIVED)


def _stale_threshold(today: date) -> date:
    return today - timedelta(days=STALE_AFTER_DAYS)


RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        key="owner_required",
        label="担当を空にしない",
        guidance="担当が空のタスクは、誰も動かないまま期限を越える。",
        applies=lambda task, today: _is_active(task),
        violates=lambda task, today: not task.owner.strip(),
    ),
    RuleSpec(
        key="due_date_required",
        label="期限を必ず入れる",
        guidance="期限が無いタスクは遅延検知の対象にならない。検知の穴になる。",
        applies=lambda task, today: _is_active(task),
        violates=lambda task, today: task.planned_end is None,
    ),
    RuleSpec(
        key="blocked_needs_ball_holder",
        label="Blocked にはボール保持者を書く",
        guidance="誰の返答待ちかが分からないブロックは、待っているだけで解けない。",
        applies=lambda task, today: task.status == WbsTask.Status.BLOCKED,
        violates=lambda task, today: not task.ball_holder.strip(),
    ),
    RuleSpec(
        key="blocked_needs_next_action",
        label="Blocked には次アクションを書く",
        guidance="次に何をすれば解けるかが書かれていないと、週次で同じ確認を繰り返す。",
        applies=lambda task, today: task.status == WbsTask.Status.BLOCKED,
        violates=lambda task, today: not task.next_action.strip(),
    ),
    RuleSpec(
        key="weekly_update",
        label=f"進行中タスクを{STALE_AFTER_DAYS}日以内に更新する",
        guidance="週次定例で棚卸しする運用。更新が止まったタスクはサイレント炎上の兆候になる。",
        applies=lambda task, today: task.status == WbsTask.Status.IN_PROGRESS,
        violates=lambda task, today: timezone.localdate(task.updated_at) < _stale_threshold(today),
    ),
    RuleSpec(
        key="progress_consistency",
        label="状態と進捗率を食い違わせない",
        guidance="完了なのに進捗が100%未満、または進捗100%なのに未完了だと、集計がどちらを信じてよいか分からない。",
        applies=lambda task, today: task.status != WbsTask.Status.ARCHIVED,
        violates=lambda task, today: (
            task.status == WbsTask.Status.DONE and task.progress_percent < Decimal("100")
        )
        or (task.status != WbsTask.Status.DONE and task.progress_percent >= Decimal("100")),
    ),
)


@dataclass(frozen=True)
class RuleResult:
    """ルール 1 件の遵守状況。"""

    spec: RuleSpec
    target_count: int
    violations: tuple[WbsTask, ...]

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def guidance(self) -> str:
        return self.spec.guidance

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    @property
    def samples(self) -> tuple[WbsTask, ...]:
        return self.violations[:MAX_SAMPLES]

    @property
    def truncated(self) -> bool:
        return self.violation_count > MAX_SAMPLES

    @property
    def compliance_percent(self) -> int:
        """対象 0 件のときは 100% ではなく「対象なし」として扱う（`has_target`）。"""

        if not self.target_count:
            return 0

        return round(100 * (self.target_count - self.violation_count) / self.target_count)

    @property
    def has_target(self) -> bool:
        return self.target_count > 0

    @property
    def tone(self) -> str:
        if not self.has_target:
            return "n"

        if not self.violation_count:
            return "g"

        return "a" if self.compliance_percent >= WARN_PERCENT else "r"

    @property
    def state_label(self) -> str:
        if not self.has_target:
            return "対象なし"

        if not self.violation_count:
            return "遵守"

        return f"違反 {self.violation_count}件"


@dataclass(frozen=True)
class InputRuleReport:
    results: tuple[RuleResult, ...]
    task_total: int
    today: date

    @property
    def violation_total(self) -> int:
        return sum(result.violation_count for result in self.results)

    @property
    def broken_rules(self) -> tuple[RuleResult, ...]:
        return tuple(result for result in self.results if result.violation_count)

    @property
    def compliance_percent(self) -> int:
        """対象のあるルールの遵守率の平均。ルール間の重みは付けない。"""

        applicable = [result for result in self.results if result.has_target]

        if not applicable:
            return 0

        return round(sum(result.compliance_percent for result in applicable) / len(applicable))

    @property
    def tone(self) -> str:
        if not self.task_total:
            return "n"

        if not self.violation_total:
            return "g"

        return "a" if self.compliance_percent >= WARN_PERCENT else "r"

    @property
    def summary(self) -> str:
        if not self.task_total:
            return "タスクが登録されていません。"

        if not self.violation_total:
            return "入力ルールの違反はありません。"

        return (
            f"{len(self.broken_rules)}件のルールに違反があります"
            f"（合計 {self.violation_total}件）。"
        )


def build_input_rule_report(
    projects: QuerySet[Project], today: date | None = None
) -> InputRuleReport:
    """参照できる案件のタスクを、入力ルールごとに判定する。

    タスクを 1 度だけ読み、ルールごとにメモリ上で振り分ける。ルールが増えても
    クエリ本数は増えない。
    """

    today = today or timezone.localdate()
    tasks = list(
        WbsTask.objects.filter(project__in=projects)
        .exclude(status=WbsTask.Status.ARCHIVED)
        .select_related("project")
        .order_by("project__name", "wbs_code")
    )

    results = []

    for spec in RULES:
        targets = [task for task in tasks if spec.applies(task, today)]
        violations = tuple(task for task in targets if spec.violates(task, today))
        results.append(
            RuleResult(spec=spec, target_count=len(targets), violations=violations)
        )

    return InputRuleReport(results=tuple(results), task_total=len(tasks), today=today)
