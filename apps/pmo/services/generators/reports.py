"""週次・月次・品質レポートの本文組み立て。

数字は `facts.py` が数えたものだけを使う。ここで再計算しない
（同じ値の数え方が 2 箇所にあると必ずズレるため）。
"""

from __future__ import annotations

from datetime import date, timedelta

from apps.pmo.services.generators.base import (
    NO_MATERIAL_HEADLINE,
    GeneratedDocument,
    Section,
    render_document,
    spec_for,
)
from apps.pmo.services.generators.facts import ProjectFacts

#: 週次レポートが振り返る期間（日）。月次は 30 日。
PERIOD_DAYS = {"weekly_report": 7, "monthly_report": 30, "quality_report": 30}


def build_report(facts: ProjectFacts, generator_key: str) -> GeneratedDocument:
    """週次・月次・品質レポートを組み立てる。"""

    spec = spec_for(generator_key)
    period_days = PERIOD_DAYS.get(generator_key, 7)
    start = facts.today - timedelta(days=period_days)
    title = f"{spec.label} {facts.project.name}（{start} 〜 {facts.today}）"

    if not facts.has_material:
        return _empty(facts, generator_key, title)

    sections = [
        _progress_section(facts),
        _issue_section(facts),
        _risk_section(facts),
        _quality_section(facts),
        _alert_section(facts),
        _next_action_section(facts),
    ]

    if generator_key == "quality_report":
        # 品質レポートは品質・不具合を先頭へ出す。読み手（品質保証）が
        # 最初に見る情報を上に置くため。
        sections = [sections[3], sections[1], sections[0], sections[2], sections[4], sections[5]]

    footer = _footer(facts, start)

    return GeneratedDocument(
        generator_key=generator_key,
        deliverable_kind=spec.deliverable_kind,
        title=title,
        body=render_document(title, sections, footer),
        evidence=tuple(facts.evidence),
        warnings=_warnings(facts),
        has_material=True,
    )


def _empty(facts: ProjectFacts, generator_key: str, title: str) -> GeneratedDocument:
    """材料ゼロのときも本文を作る。空にせず「何が無いか」を書く。"""

    spec = spec_for(generator_key)
    body = render_document(
        title,
        [
            Section(
                NO_MATERIAL_HEADLINE,
                [
                    "WBSタスク・課題・リスク・不具合・品質指標・アラートのいずれも登録されていません。",
                    "案件データを取り込むか、手入力してから再生成してください。",
                ],
            )
        ],
    )

    return GeneratedDocument(
        generator_key=generator_key,
        deliverable_kind=spec.deliverable_kind,
        title=title,
        body=body,
        evidence=(),
        warnings=("生成に使える実データが 1 件もありません。",),
        has_material=False,
    )


def _progress_section(facts: ProjectFacts) -> Section:
    lines: list[str] = []

    if facts.task_total:
        lines.append(
            f"平均進捗 {facts.task_progress_percent}%（タスク {facts.task_total}件、"
            f"完了 {facts.task_done}件／完了率 {facts.task_done_percent}%）"
        )
        lines.append(
            f"期限超過 {facts.task_overdue}件"
            f"（うちクリティカルパス上 {facts.task_overdue_critical}件）"
        )

        if facts.task_blocked:
            lines.append(f"ブロック中 {facts.task_blocked}件")

        for task in facts.overdue_tasks[:5]:
            mark = "【CP】" if task.is_critical_path else ""
            owner = task.owner or "担当未設定"
            lines.append(
                f"{mark}{task.wbs_code} {task.name}／期限 {task.planned_end}／{owner}"
            )

    if facts.milestones:
        lines.append(
            f"マイルストーン {len(facts.milestones)}件、後ろ倒し {facts.milestone_late}件"
        )

    return Section("進捗", lines)


def _issue_section(facts: ProjectFacts) -> Section:
    lines: list[str] = []

    if facts.issue_open:
        lines.append(
            f"未解決 {facts.issue_open}件（高・重大 {facts.issue_high}件、"
            f"期限超過 {facts.issue_overdue}件）"
        )

        for issue in facts.open_issues[:5]:
            lines.append(
                f"[{issue.get_severity_display()}] {issue.title}／"
                f"{issue.owner or '担当未設定'}／期限 {issue.due_date or '未設定'}"
            )

    return Section("課題", lines)


def _risk_section(facts: ProjectFacts) -> Section:
    lines: list[str] = []

    if facts.risk_open:
        lines.append(
            f"オープン {facts.risk_open}件（高リスク {facts.risk_high}件、"
            f"顕在化 {facts.risk_materialized}件）"
        )

        for risk in facts.high_risks[:5]:
            score = risk.probability * risk.impact
            lines.append(
                f"[スコア{score}] {risk.title}／対応方針: {risk.mitigation or '未記入'}"
            )

    return Section("リスク", lines)


def _quality_section(facts: ProjectFacts) -> Section:
    lines: list[str] = []

    if facts.defect_total:
        breakdown = "、".join(f"{k} {v}件" for k, v in facts.defect_by_severity.items())
        lines.append(
            f"不具合 {facts.defect_total}件（{breakdown}）／未クローズ {facts.defect_open}件"
        )

    for metric in facts.metrics:
        target = "" if metric.target_value is None else f"／目標 {metric.target_value}"
        judged = "未達" if metric in facts.metric_miss else "達成"
        judged = judged if metric.target_value is not None else "目標未設定"
        lines.append(
            f"{metric.metric_label or metric.metric_key}: {metric.value}{metric.unit}"
            f"{target}（{judged}／計測 {metric.measured_on}）"
        )

    return Section("品質", lines)


def _alert_section(facts: ProjectFacts) -> Section:
    lines: list[str] = []

    if facts.alert_open:
        lines.append(f"未解消 {facts.alert_open}件（重大 {facts.alert_critical}件）")

        for alert in facts.open_alerts[:5]:
            lines.append(
                f"[{alert.get_severity_display()}] {alert.title}"
                f"／{alert.get_category_display()}"
            )

    return Section("アラート", lines)


def _next_action_section(facts: ProjectFacts) -> Section:
    """次アクションは推測せず、DB に入力されている値だけを転記する。"""

    lines = [
        f"{task.wbs_code} {task.name}: {task.next_action}"
        f"（ボール: {task.ball_holder or '未設定'}）"
        for task in facts.overdue_tasks
        if task.next_action
    ][:5]

    return Section("次アクション（WBS の入力値）", lines)


def _footer(facts: ProjectFacts, start: date) -> str:
    return (
        "―――\n"
        f"作成日: {facts.today}／対象期間: {start} 〜 {facts.today}\n"
        "本文の数値は登録データからの集計です。算出根拠は処理トレースを参照してください。"
    )


def _warnings(facts: ProjectFacts) -> tuple[str, ...]:
    """人が直す前に気づくべき点。データの欠落を「無い」と書かない。"""

    warnings: list[str] = []

    if not facts.task_total:
        warnings.append("WBSタスクが未登録のため、進捗の数値は空欄です。")

    if not facts.metrics:
        warnings.append("品質指標が未登録のため、品質の判定は不具合件数のみです。")

    if facts.task_overdue_critical:
        warnings.append(
            f"クリティカルパス上の期限超過が {facts.task_overdue_critical}件あります。"
        )

    return tuple(warnings)
