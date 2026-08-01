"""入力標準ルールの運用支援。

PMO の実務で最も時間を食うのは「メンバーに WBS を更新させること」で、
更新されていないデータをいくら集計しても意思決定には使えない。
そこで「守られているか」ではなく **「誰に催促すればよいか」** を出力する。

判定は実データのみで行い、AI は使わない（AI_PROVIDER に依存しない）。
しきい値と有効ルールは `settings.OPS_RULES` に置き、ここへ数値を埋め込まない。

除外の考え方:
    - アーカイブ済みは運用対象外なので、全ルールの母数から外す。
    - 完了済みを「未更新＝サボり」と数えない。終わった仕事は更新されなくて当然。
      ただし「根拠なし」だけは完了済みも対象にする（完了の根拠が残っていない、が論点のため）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from apps.projects.models import WbsTask

#: 担当も ボール保持者 も空のタスクを束ねる表示名。空文字のままだと画面で消える。
UNASSIGNED_LABEL = "未割当"

#: ルールの表示名と説明。設定側には「有効/無効」だけを置き、文言はここで持つ。
RULE_LABELS: dict[str, tuple[str, str]] = {
    "stale_update": ("定期更新", "締め曜日以降に更新されていない"),
    "blocked_handling": ("Blocked運用", "ブロック中なのにボール保持者か次アクションが空"),
    "overdue_status": ("期限管理", "期限が過ぎているのに状態が更新されていない"),
    "missing_owner": ("担当不在", "進行中なのに担当が空"),
    "missing_evidence": ("根拠なし", "進捗が完了水準なのに根拠メモが空"),
}

#: ルールごとの重大度（バッジの色）。運用が止まるものを赤、催促で済むものを橙にする。
RULE_TONES: dict[str, str] = {
    "stale_update": "a",
    "blocked_handling": "r",
    "overdue_status": "r",
    "missing_owner": "a",
    "missing_evidence": "a",
}

#: 運用対象から外す状態。アーカイブは「もう触らない」宣言なので母数に入れない。
EXCLUDED_STATUSES = (WbsTask.Status.ARCHIVED,)

#: 完了とみなす状態。未更新の催促対象から外す。
FINISHED_STATUSES = (WbsTask.Status.DONE, WbsTask.Status.ARCHIVED)


@dataclass(frozen=True)
class RuleViolation:
    """1 タスク 1 ルールの違反。誰に何を言えばよいかが 1 行で分かる粒度で持つ。"""

    rule: str
    task: WbsTask
    detail: str

    @property
    def label(self) -> str:
        return RULE_LABELS[self.rule][0]

    @property
    def tone(self) -> str:
        return RULE_TONES.get(self.rule, "a")

    @property
    def assignee(self) -> str:
        """催促先。担当が空ならボール保持者、どちらも空なら未割当。"""

        return self.task.owner or self.task.ball_holder or UNASSIGNED_LABEL


@dataclass(frozen=True)
class RuleSummary:
    """ルール単位の件数。どの運用が崩れているかを見るために持つ。"""

    rule: str
    count: int

    @property
    def label(self) -> str:
        return RULE_LABELS[self.rule][0]

    @property
    def description(self) -> str:
        return RULE_LABELS[self.rule][1]

    @property
    def tone(self) -> str:
        return RULE_TONES.get(self.rule, "a")


@dataclass(frozen=True)
class AssigneeSummary:
    """担当者ごとの未更新一覧。催促は人単位でしか行えないため、人で束ねる。"""

    assignee: str
    violations: tuple[RuleViolation, ...]

    @property
    def total(self) -> int:
        return len(self.violations)

    @property
    def task_count(self) -> int:
        """違反しているタスクの実数。1 タスクが複数ルールに触れても 1 と数える。"""

        return len({violation.task.pk for violation in self.violations})

    @property
    def rule_counts(self) -> tuple[RuleSummary, ...]:
        counts: dict[str, int] = {}

        for violation in self.violations:
            counts[violation.rule] = counts.get(violation.rule, 0) + 1

        return tuple(
            RuleSummary(rule=rule, count=count)
            for rule, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        )

    @property
    def tone(self) -> str:
        """催促の優先度。赤ルールを含むか、件数が多い相手を上位に見せる。"""

        if any(violation.tone == "r" for violation in self.violations):
            return "r"

        return "a" if self.total >= 3 else "n"


@dataclass(frozen=True)
class OpsRulesReport:
    """入力標準ルールの判定結果。"""

    checked_on: date
    update_cutoff: date
    target_tasks: int
    assignees: tuple[AssigneeSummary, ...]
    rule_summaries: tuple[RuleSummary, ...]
    enabled_rules: tuple[str, ...]

    @property
    def has_targets(self) -> bool:
        """判定対象が存在するか。

        0 件のときに「全員が守っている」と出してはいけない。
        測っていないことと守られていることを混同させないため、明示的に持つ。
        """

        return self.target_tasks > 0

    @property
    def total_violations(self) -> int:
        return sum(summary.total for summary in self.assignees)

    @property
    def violating_tasks(self) -> int:
        return sum(summary.task_count for summary in self.assignees)

    @property
    def compliance_percent(self) -> int | None:
        """ルールを守れているタスクの割合。対象 0 件なら割合を出さない。"""

        if not self.has_targets:
            return None

        return round(100 * (self.target_tasks - self.violating_tasks) / self.target_tasks)

    @property
    def worst(self) -> AssigneeSummary | None:
        """最も催促が必要な相手。"""

        return self.assignees[0] if self.assignees else None

    @property
    def status_note(self) -> str:
        """画面と管理コマンドで同じ文言を使うための要約。"""

        if not self.has_targets:
            return "対象なし（判定できるタスクがありません）"

        if not self.total_violations:
            return "違反なし（対象タスクはすべてルールを満たしています）"

        return f"{len(self.assignees)}名 / {self.violating_tasks}件のタスクに未対応があります"


def enabled_rules() -> tuple[str, ...]:
    """有効なルールキー。設定に無いキーは無視し、表示順を固定する。"""

    flags = settings.OPS_RULES.get("ENABLED", {})

    return tuple(rule for rule in RULE_LABELS if flags.get(rule, False))


def update_cutoff_date(today: date) -> date:
    """定期更新の基準日。

    「金曜更新」は毎週金曜の締めを指すので、直近の締め曜日（今日を含む）まで
    さかのぼり、猶予日数を引いた日を基準にする。これより前の更新は未更新扱い。
    """

    rules = settings.OPS_RULES
    weekday = int(rules.get("UPDATE_WEEKDAY", 4)) % 7
    grace = int(rules.get("UPDATE_GRACE_DAYS", 0))
    offset = (today.weekday() - weekday) % 7

    return today - timedelta(days=offset + grace)


def build_ops_rules_report(
    tasks: QuerySet[WbsTask], *, today: date | None = None
) -> OpsRulesReport:
    """入力標準ルールを判定し、担当者別にまとめる。

    引数の QuerySet はテナント分離済みであることを前提にする
    （呼び出し側が `scoped_projects_for()` 由来の案件で絞る）。
    """

    checked_on = today or timezone.localdate()
    cutoff = update_cutoff_date(checked_on)
    rules = enabled_rules()

    targets = [task for task in tasks if task.status not in EXCLUDED_STATUSES]
    violations: list[RuleViolation] = []

    for task in targets:
        violations.extend(_check_task(task, checked_on=checked_on, cutoff=cutoff, rules=rules))

    return OpsRulesReport(
        checked_on=checked_on,
        update_cutoff=cutoff,
        target_tasks=len(targets),
        assignees=_group_by_assignee(violations),
        rule_summaries=_summarize_rules(violations, rules),
        enabled_rules=rules,
    )


def _check_task(
    task: WbsTask, *, checked_on: date, cutoff: date, rules: tuple[str, ...]
) -> list[RuleViolation]:
    """1 タスクに対する全ルールの判定。"""

    checks = (
        ("stale_update", _check_stale_update(task, cutoff=cutoff)),
        ("blocked_handling", _check_blocked_handling(task)),
        ("overdue_status", _check_overdue_status(task, checked_on=checked_on)),
        ("missing_owner", _check_missing_owner(task)),
        ("missing_evidence", _check_missing_evidence(task)),
    )

    return [
        RuleViolation(rule=rule, task=task, detail=detail)
        for rule, detail in checks
        if rule in rules and detail
    ]


def _check_stale_update(task: WbsTask, *, cutoff: date) -> str:
    """締め曜日以降に更新されていないタスク。完了済みは催促の対象にしない。"""

    if task.status in FINISHED_STATUSES:
        return ""

    # USE_TZ の設定に依存させない。naive のまま localtime に渡すと例外になる。
    updated_at = task.updated_at
    updated_on = (
        timezone.localtime(updated_at).date()
        if timezone.is_aware(updated_at)
        else updated_at.date()
    )

    if updated_on >= cutoff:
        return ""

    return f"最終更新 {updated_on:%Y-%m-%d}（基準 {cutoff:%Y-%m-%d}）"


def _check_blocked_handling(task: WbsTask) -> str:
    """ブロック中は「誰が」「次に何を」が無いと動かない。両方そろって初めて運用。"""

    if task.status != WbsTask.Status.BLOCKED:
        return ""

    missing = []

    if not task.ball_holder.strip():
        missing.append("ボール保持者")

    if not task.next_action.strip():
        missing.append("次アクション")

    return f"ブロック中だが {' と '.join(missing)} が未設定" if missing else ""


def _check_overdue_status(task: WbsTask, *, checked_on: date) -> str:
    """期限が過ぎているのに完了にも延期にもなっていない（状態が現実と合っていない）。"""

    if task.planned_end is None or task.status in FINISHED_STATUSES:
        return ""

    grace = int(settings.OPS_RULES.get("OVERDUE_GRACE_DAYS", 0))
    overdue_days = (checked_on - task.planned_end).days

    if overdue_days <= grace:
        return ""

    return f"期限 {task.planned_end:%Y-%m-%d} を {overdue_days}日超過（状態: {task.get_status_display()}）"


def _check_missing_owner(task: WbsTask) -> str:
    """進行中なのに担当が空。誰も持っていない作業は必ず落ちる。"""

    if task.status != WbsTask.Status.IN_PROGRESS or task.owner.strip():
        return ""

    return "進行中だが担当が未設定"


def _check_missing_evidence(task: WbsTask) -> str:
    """完了水準の進捗なのに根拠メモが空。根拠追跡ができない完了は監査で戻される。"""

    threshold = int(settings.OPS_RULES.get("EVIDENCE_REQUIRED_PERCENT", 100))

    if task.progress_percent is None or float(task.progress_percent) < threshold:
        return ""

    if task.evidence_note.strip():
        return ""

    return f"進捗 {float(task.progress_percent):.0f}% だが根拠メモが空"


def _group_by_assignee(violations: list[RuleViolation]) -> tuple[AssigneeSummary, ...]:
    """催促先ごとに束ねる。件数の多い順、同数ならで名前順で安定させる。"""

    buckets: dict[str, list[RuleViolation]] = {}

    for violation in violations:
        buckets.setdefault(violation.assignee, []).append(violation)

    summaries = [
        AssigneeSummary(assignee=assignee, violations=tuple(items))
        for assignee, items in buckets.items()
    ]

    return tuple(sorted(summaries, key=lambda s: (-s.total, s.assignee)))


def _summarize_rules(
    violations: list[RuleViolation], rules: tuple[str, ...]
) -> tuple[RuleSummary, ...]:
    """ルール別の件数。0 件のルールも「見ている」ことを示すために残す。"""

    counts = {rule: 0 for rule in rules}

    for violation in violations:
        counts[violation.rule] = counts.get(violation.rule, 0) + 1

    return tuple(RuleSummary(rule=rule, count=counts[rule]) for rule in rules)
