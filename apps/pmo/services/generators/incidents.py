"""障害サマリーの本文組み立て。

重大度別の件数・未解決の一覧・検出工程の偏りの 3 点に絞る。
偏りは「1 工程に何%集中しているか」で示す。件数の羅列だけでは、
上流でのレビュー不足なのか試験の網羅不足なのかが読み取れないため。
"""

from __future__ import annotations

from apps.pmo.services.generators.base import (
    NO_MATERIAL_HEADLINE,
    GeneratedDocument,
    Section,
    percent,
    render_document,
    spec_for,
)
from apps.pmo.services.generators.facts import ProjectFacts

GENERATOR_KEY = "incident_summary"

#: 1 つの検出工程にこの割合以上が集中していたら「偏りあり」とする。
PHASE_CONCENTRATION_THRESHOLD = 50.0


def build_incident_summary(facts: ProjectFacts) -> GeneratedDocument:
    """不具合データから障害サマリーを作る。"""

    spec = spec_for(GENERATOR_KEY)
    title = f"障害サマリー {facts.project.name}（{facts.today} 時点）"

    if not facts.defect_total:
        body = render_document(
            title,
            [
                Section(
                    NO_MATERIAL_HEADLINE,
                    ["不具合が 1 件も登録されていません。取り込み後に再生成してください。"],
                )
            ],
        )

        return GeneratedDocument(
            generator_key=GENERATOR_KEY,
            deliverable_kind=spec.deliverable_kind,
            title=title,
            body=body,
            evidence=(),
            warnings=("不具合データが 0 件です。",),
            has_material=False,
        )

    sections = [
        _severity_section(facts),
        _open_section(facts),
        _phase_section(facts),
    ]
    footer = (
        "―――\n"
        f"作成日: {facts.today}／対象: {facts.project.name} の不具合 {facts.defect_total}件\n"
        "件数は登録済みの不具合レコードの実数です。"
    )

    return GeneratedDocument(
        generator_key=GENERATOR_KEY,
        deliverable_kind=spec.deliverable_kind,
        title=title,
        body=render_document(title, sections, footer),
        evidence=tuple(
            item for item in facts.evidence if item.source == "projects.Defect"
        ),
        warnings=_warnings(facts),
        has_material=True,
    )


def _severity_section(facts: ProjectFacts) -> Section:
    lines = [
        f"総数 {facts.defect_total}件／未クローズ {facts.defect_open}件"
        f"（未クローズ率 {percent(facts.defect_open, facts.defect_total)}%）"
    ]
    lines += [
        f"{label}: {count}件（{percent(count, facts.defect_total)}%）"
        f"／未クローズ {facts.defect_open_by_severity.get(label, 0)}件"
        for label, count in facts.defect_by_severity.items()
    ]

    return Section("重大度別の件数", lines)


def _open_section(facts: ProjectFacts) -> Section:
    lines = [
        f"[{defect.get_severity_display()}] {defect.title}"
        f"／状態 {defect.get_status_display()}"
        f"／検出工程 {defect.phase or '未記入'}"
        f"／検出日 {defect.detected_on or '未記入'}"
        for defect in facts.open_defects
    ]

    return Section(f"未解決の不具合（{facts.defect_open}件）", lines)


def _phase_section(facts: ProjectFacts) -> Section:
    lines = [
        f"{phase}: {count}件（{percent(count, facts.defect_total)}%）"
        for phase, count in sorted(
            facts.defect_by_phase.items(), key=lambda pair: -pair[1]
        )
    ]
    top = _top_phase(facts)

    if top:
        phase, count = top
        lines.append(
            f"→ {phase} に {percent(count, facts.defect_total)}% が集中しています。"
            "当該工程の作り込み・検出方法の見直しを検討してください。"
        )

    return Section("検出工程の分布", lines)


def _top_phase(facts: ProjectFacts) -> tuple[str, int] | None:
    """閾値を超えて集中している工程。無ければ None。"""

    if not facts.defect_by_phase:
        return None

    phase, count = max(facts.defect_by_phase.items(), key=lambda pair: pair[1])

    if percent(count, facts.defect_total) < PHASE_CONCENTRATION_THRESHOLD:
        return None

    return phase, count


def _warnings(facts: ProjectFacts) -> tuple[str, ...]:
    warnings: list[str] = []
    unknown_phase = facts.defect_by_phase.get("未記入", 0)

    if unknown_phase:
        warnings.append(
            f"検出工程が未記入の不具合が {unknown_phase}件あり、工程の偏り分析が不正確です。"
        )

    critical_open = facts.defect_open_by_severity.get("重大", 0)

    if critical_open:
        warnings.append(f"重大な不具合が {critical_open}件未クローズです。")

    return tuple(warnings)
