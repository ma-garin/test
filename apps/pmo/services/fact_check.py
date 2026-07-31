"""成果物本文の事実チェック（要件 #15）。

赤字率（`diffing.py`）は「人がどれだけ直したか」を測るが、
**直した結果が正しいか**は見ていない。ここはそこを見る。

やり方は単純で、本文に書かれた「N件」「N%」「N日」を取り出し、
案件の実データ（`generators.facts`）から数え直した値と突き合わせる。
一致しない数字は「裏が取れない」として挙げる。

LLM は使わない。使うと、チェック自体が誤りうるものになり、
「チェックを通ったから正しい」と言えなくなる。ここは決定的でなければ意味がない。

**限界を先に書いておく。** この方式で見つかるのは数値の不一致だけである。
「原因は要員不足である」のような、数字を伴わない断定は検出できない。
画面でもそのように表示する（`UNCHECKABLE_NOTE`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from apps.pmo.services.generators.facts import ProjectFacts, collect_facts
from apps.pmo.services.generators.reports import PERIOD_DAYS
from apps.projects.models import WbsTask

#: 本文から拾う「数値 + 単位」。単位が無い数字（WBSコード、版数、日付の一部）は
#: 主張とは限らないので拾わない。拾うと誤検知だらけになり、誰も見なくなる。
CLAIM_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(件|%|％|日)")

#: 単位の表記揺れを 1 つに寄せる。
UNIT_ALIASES = {"％": "%"}

#: この方式で検出できないことの説明。画面に必ず添える。
UNCHECKABLE_NOTE = "数値を伴わない主張（原因の断定、見通しなど）はこの方式では検証できません。"


@dataclass(frozen=True)
class Claim:
    """本文中の 1 つの数値主張。"""

    value: float
    unit: str
    line: str
    verified: bool

    @property
    def display(self) -> str:
        return f"{_format_number(self.value)}{self.unit}"

    @property
    def tone(self) -> str:
        return "g" if self.verified else "r"


@dataclass(frozen=True)
class FactCheckResult:
    """1 件の成果物に対するチェック結果。"""

    claims: tuple[Claim, ...] = ()
    checked_body: str = ""
    has_material: bool = True

    @property
    def total(self) -> int:
        return len(self.claims)

    @property
    def unverified(self) -> tuple[Claim, ...]:
        return tuple(claim for claim in self.claims if not claim.verified)

    @property
    def unverified_count(self) -> int:
        return len(self.unverified)

    @property
    def verified_count(self) -> int:
        return self.total - self.unverified_count

    @property
    def passed(self) -> bool:
        """PoC の受入条件は「事実誤認 0 件」。1 件でも通さない。"""

        return self.unverified_count == 0

    @property
    def tone(self) -> str:
        if not self.total:
            return "n"

        return "g" if self.passed else "r"

    @property
    def summary(self) -> str:
        if not self.checked_body:
            return "本文がまだありません。"

        if not self.total:
            return "検証できる数値が本文にありません。"

        if self.passed:
            return f"数値 {self.total} 件すべてが実データと一致しました。"

        return f"数値 {self.total} 件のうち {self.unverified_count} 件が実データと一致しません。"


def _format_number(value: float) -> str:
    """1 桁の小数だけ残す。62.0 を「62」と書けるようにする。"""

    if value == int(value):
        return str(int(value))

    return str(round(value, 1))


def _normalize_unit(unit: str) -> str:
    return UNIT_ALIASES.get(unit, unit)


def _counts(facts: ProjectFacts) -> set[float]:
    """「N件」として本文に出てよい値。"""

    values = {
        facts.task_total,
        facts.task_done,
        facts.task_blocked,
        facts.task_overdue,
        facts.task_overdue_critical,
        facts.issue_open,
        facts.issue_high,
        facts.issue_overdue,
        facts.risk_open,
        facts.risk_high,
        facts.risk_materialized,
        facts.defect_total,
        facts.defect_open,
        facts.alert_open,
        facts.alert_critical,
        facts.milestone_late,
        len(facts.milestones),
        len(facts.metrics),
        len(facts.metric_miss),
        len(facts.overdue_tasks),
        len(facts.open_issues),
        len(facts.high_risks),
        len(facts.open_defects),
        len(facts.open_alerts),
    }
    values.update(facts.defect_by_severity.values())
    values.update(facts.defect_open_by_severity.values())
    values.update(facts.defect_by_phase.values())
    values.update(
        {
            facts.period_task_done,
            facts.period_issue_opened,
            facts.period_issue_resolved,
            facts.period_defect_detected,
            facts.period_defect_closed,
            facts.period_alert_detected,
        }
    )

    return {float(value) for value in values}


def _percents(facts: ProjectFacts) -> set[float]:
    """「N%」として本文に出てよい値。"""

    values = {
        facts.task_progress_percent,
        facts.task_done_percent,
        float(getattr(facts.project, "progress_percent", 0) or 0),
    }

    for metric in facts.metrics:
        for attribute in ("value", "target_value"):
            raw = getattr(metric, attribute, None)

            if raw is not None:
                values.add(float(raw))

    return {float(value) for value in values}


def _days(facts: ProjectFacts) -> set[float]:
    """「N日」として本文に出てよい値。超過日数とマイルストーンのずれ。"""

    values: set[float] = set()

    for task in facts.overdue_tasks:
        if task.planned_end:
            values.add(float((facts.today - task.planned_end).days))

    for milestone in facts.milestones:
        reference = milestone.actual_date or milestone.forecast_date

        if reference:
            values.add(float((reference - milestone.planned_date).days))

    for task in WbsTask.objects.filter(project=facts.project).exclude(
        status=WbsTask.Status.ARCHIVED
    ):
        if task.planned_start and task.planned_end:
            values.add(float((task.planned_end - task.planned_start).days))

    return values


def _allowed(facts: ProjectFacts) -> dict[str, set[float]]:
    return {"件": _counts(facts), "%": _percents(facts), "日": _days(facts)}


def check(deliverable, *, today=None) -> FactCheckResult:
    """成果物 1 件の本文を、案件の実データと突き合わせる。

    確定本文があればそれを、無ければ AI 生成本文を見る。人が直した後の本文こそ
    検証したいものなので、確定本文を優先する。
    """

    body = deliverable.body or deliverable.ai_generated_body or ""

    if not body.strip():
        return FactCheckResult(checked_body="")

    # 成果物には「どの期間を振り返ったか」が残らないため、レポートが使う
    # 期間（週次 7 日・月次 30 日）すべてで数え直し、その和集合を正とする。
    # 期間を特定できない以上、狭めると正しい数字を誤りと判定してしまう。
    facts = collect_facts(deliverable.project, today)
    allowed = _allowed(facts)

    for days in sorted(set(PERIOD_DAYS.values())):
        period_facts = collect_facts(
            deliverable.project, today, period_start=facts.today - timedelta(days=days)
        )

        for unit, extra in _allowed(period_facts).items():
            allowed[unit] = allowed.get(unit, set()) | extra

    claims: list[Claim] = []

    for line in body.splitlines():
        for raw_value, raw_unit in CLAIM_PATTERN.findall(line):
            unit = _normalize_unit(raw_unit)
            value = float(raw_value)
            claims.append(
                Claim(
                    value=value,
                    unit=unit,
                    line=line.strip(),
                    verified=value in allowed.get(unit, set()),
                )
            )

    return FactCheckResult(
        claims=tuple(claims), checked_body=body, has_material=facts.has_material
    )
