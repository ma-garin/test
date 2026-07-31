"""計画ドラフト生成（トレーサビリティ #64）。

案件の WBS から、フェーズ・マイルストーン案・レビュー観点を組む。
マイルストーンは「フェーズ内タスクの計画終了日の最大値」という 1 つの規則だけで
導出する。規則を増やすと、なぜその日付になったのかを人が追えなくなるため。
"""

from __future__ import annotations

from apps.pmo.services.generators.base import (
    NO_MATERIAL_HEADLINE,
    EvidenceItem,
    GeneratedDocument,
    Section,
    render_document,
    spec_for,
)
from apps.pmo.services.generators.facts import ProjectFacts
from apps.projects.models import WbsTask

GENERATOR_KEY = "plan_draft"

#: フェーズ名に含まれていたら、その工程固有のレビュー観点を足すキーワード。
PHASE_REVIEW_HINTS: tuple[tuple[str, str], ...] = (
    ("要件", "要件の抜け漏れとトレーサビリティ（要件 → 設計 → テスト）"),
    ("設計", "設計レビューの実施記録と指摘のクローズ状況"),
    ("実装", "コードレビュー基準と静的解析の適用範囲"),
    ("テスト", "テスト観点の網羅性と、不具合の収束判定基準"),
    ("試験", "テスト観点の網羅性と、不具合の収束判定基準"),
    ("移行", "移行リハーサルの回数と切り戻し手順"),
    ("運用", "運用引き継ぎ資料と問い合わせ窓口の確定"),
)

#: 工程に関わらず必ず確認する観点（PMBOK の計画プロセス群を意識した固定項目）。
BASE_REVIEW_POINTS: tuple[str, ...] = (
    "体制と役割分担（PM／PMO／各チームの責任範囲）",
    "変更管理プロセス（変更要求の起票・影響分析・承認の流れ）",
    "品質指標の目標値と、未達時のエスカレーション先",
)


def build_plan_draft(facts: ProjectFacts) -> GeneratedDocument:
    """WBS からマイルストーンとレビュー観点を組み立てる。"""

    spec = spec_for(GENERATOR_KEY)
    project = facts.project
    title = f"計画ドラフト {project.name}（{facts.today} 起案）"
    phases = _phases(project)

    if not phases and not facts.milestones:
        body = render_document(
            title,
            [
                Section(
                    NO_MATERIAL_HEADLINE,
                    [
                        "WBSタスクもマイルストーンも登録されていません。",
                        "WBSを登録してから計画ドラフトを生成してください。",
                    ],
                )
            ],
        )

        return GeneratedDocument(
            generator_key=GENERATOR_KEY,
            deliverable_kind=spec.deliverable_kind,
            title=title,
            body=body,
            evidence=(),
            warnings=("WBSタスクが 0 件のため、マイルストーンを導出できません。",),
            has_material=False,
        )

    review_points = _review_points(facts, phases)
    sections = [
        _premise_section(facts, phases),
        _milestone_section(facts, phases),
        _review_section(review_points),
        _risk_section(facts),
    ]
    footer = (
        "―――\n"
        f"起案日: {facts.today}／出所: {project.name} の WBS {facts.task_total}件\n"
        "マイルストーン案の日付は、各フェーズ配下タスクの計画終了日の最大値です。"
    )

    return GeneratedDocument(
        generator_key=GENERATOR_KEY,
        deliverable_kind=spec.deliverable_kind,
        title=title,
        body=render_document(title, sections, footer),
        evidence=_evidence(facts, phases),
        warnings=_warnings(facts, phases),
        review_points=tuple(review_points),
        has_material=True,
    )


def _phases(project) -> list[dict]:
    """WBS をフェーズ単位へまとめる。

    親タスクがあればそれをフェーズとみなす。無ければ WBS コードの先頭要素で束ねる。
    どちらでも束ねられない案件はフェーズ 1 つに畳む。
    """

    tasks = list(
        WbsTask.objects.filter(project=project)
        .exclude(status=WbsTask.Status.ARCHIVED)
        .select_related("parent")
        .order_by("wbs_code")
    )

    if not tasks:
        return []

    groups: dict[str, list] = {}

    for task in tasks:
        if task.parent is not None:
            key = task.parent.name
        else:
            key = _code_prefix(task.wbs_code) or task.name

        groups.setdefault(key, []).append(task)

    phases = []

    for name, members in groups.items():
        ends = [t.planned_end for t in members if t.planned_end]
        phases.append(
            {
                "name": name,
                "tasks": members,
                "end": max(ends) if ends else None,
                "critical": sum(1 for t in members if t.is_critical_path),
            }
        )

    return sorted(phases, key=lambda phase: (phase["end"] is None, phase["end"], phase["name"]))


def _code_prefix(code: str) -> str:
    """WBS コードの先頭要素。"1-2-3" → "1"。"""

    for separator in ("-", ".", "_"):
        if separator in code:
            return code.split(separator)[0]

    return code


def _premise_section(facts: ProjectFacts, phases: list[dict]) -> Section:
    project = facts.project
    lines = [
        f"案件: {project.name}（コード {project.code}）",
        f"PM: {project.project_manager or '未設定'}／PMO: {project.pmo_manager or '未設定'}",
        f"計画期間: {project.planned_start or '未設定'} 〜 {project.planned_end or '未設定'}",
        f"WBSタスク {facts.task_total}件／フェーズ {len(phases)}区分",
    ]

    return Section("前提（登録済みの案件情報）", lines)


def _milestone_section(facts: ProjectFacts, phases: list[dict]) -> Section:
    lines = []

    for phase in phases:
        end = phase["end"] or "期日未設定"
        critical = f"／CP上 {phase['critical']}件" if phase["critical"] else ""
        lines.append(
            f"{end}　{phase['name']} 完了"
            f"（配下タスク {len(phase['tasks'])}件{critical}）"
        )

    for milestone in facts.milestones:
        gate = "【品質ゲート】" if milestone.is_gate else ""
        lines.append(
            f"{milestone.planned_date}　{gate}{milestone.name}（登録済み"
            f"／実績 {milestone.actual_date or '未'}）"
        )

    return Section("マイルストーン案", lines)


def _review_section(review_points: list[str]) -> Section:
    return Section("レビュー観点", review_points)


def _risk_section(facts: ProjectFacts) -> Section:
    lines = []

    if facts.task_overdue:
        lines.append(
            f"着手時点で期限超過が {facts.task_overdue}件"
            f"（うちCP上 {facts.task_overdue_critical}件）。計画の再ベースライン要否を判断する"
        )

    for risk in facts.high_risks[:5]:
        lines.append(f"[スコア{risk.probability * risk.impact}] {risk.title}")

    return Section("計画時に織り込む既知のリスク", lines)


def _review_points(facts: ProjectFacts, phases: list[dict]) -> list[str]:
    points: list[str] = []

    for phase in phases:
        for keyword, point in PHASE_REVIEW_HINTS:
            if keyword in phase["name"] and point not in points:
                points.append(f"{phase['name']}: {point}")

    if any(phase["critical"] for phase in phases):
        points.append("クリティカルパス上タスクの前提条件と、遅延時の代替案")

    if facts.task_overdue:
        points.append(f"期限超過 {facts.task_overdue}件のリカバリ計画と再ベースライン")

    if any(milestone.is_gate for milestone in facts.milestones):
        points.append("品質ゲートの合否基準と、判定者の明確化")

    points.extend(BASE_REVIEW_POINTS)

    return points


def _evidence(facts: ProjectFacts, phases: list[dict]) -> tuple[EvidenceItem, ...]:
    phase_detail = "／".join(
        f"{phase['name']}: タスク{len(phase['tasks'])}件・完了予定{phase['end'] or '未設定'}"
        for phase in phases
    )

    return tuple(facts.evidence) + (
        EvidenceItem(
            source="projects.WbsTask",
            label="フェーズ区分とマイルストーン案",
            detail=(
                f"{len(phases)}区分。各フェーズの完了予定日は配下タスクの"
                f"planned_end の最大値。{phase_detail}"
            ),
            count=len(phases),
        ),
    )


def _warnings(facts: ProjectFacts, phases: list[dict]) -> tuple[str, ...]:
    warnings: list[str] = []
    undated = [phase["name"] for phase in phases if phase["end"] is None]

    if undated:
        warnings.append(
            f"計画終了日が入っていないフェーズが {len(undated)}件あり、"
            f"マイルストーン日付を導出できません（{'、'.join(undated[:3])}）。"
        )

    if not facts.milestones:
        warnings.append("マイルストーンが未登録のため、案は WBS からの導出のみです。")

    return tuple(warnings)
