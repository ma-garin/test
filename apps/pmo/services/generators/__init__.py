"""成果物生成サービスの入口。

`build_document()` が実データから本文を組み、`generate_and_save()` がそれを
`Deliverable` として保存する。保存時に必ず `AgentRun` / `AgentStep` を作り、
本文中の数字がどのテーブルの何件から出たかを残す（根拠追跡）。

LLM は使わない（ADR-0003）。`AI_PROVIDER=local_hash` の既定でも全経路が通る。
LLM が使えるときに本文の言い回しを整える余地は残すが、**数字の算出には介在させない。**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.agents.models import AgentRun, AgentStep, EvidenceEvaluation, Intent, Level, Recommendation
from apps.pmo.models import Deliverable, PlanDraft
from apps.pmo.services.generators.base import (
    GENERATORS,
    GeneratedDocument,
    GeneratorSpec,
    generator_choices,
    spec_for,
)
from apps.pmo.services.generators.facts import collect_facts
from apps.pmo.services.generators.incidents import build_incident_summary
from apps.pmo.services.generators.minutes import build_action_items, build_minutes
from apps.pmo.services.generators.plan import build_plan_draft
from apps.pmo.services.generators.reports import build_report, period_start_for

__all__ = [
    "GENERATORS",
    "GeneratedDocument",
    "GeneratorSpec",
    "GenerationResult",
    "build_document",
    "period_start_for",
    "generate_and_save",
    "generator_choices",
    "spec_for",
]

REPORT_KEYS = ("weekly_report", "monthly_report", "quality_report")


@dataclass(frozen=True)
class GenerationResult:
    """生成の結果。画面はこれだけを見てメッセージを出す。"""

    ok: bool
    message: str
    deliverable: Deliverable | None = None
    document: GeneratedDocument | None = None


def build_document(
    project, generator_key: str, notes: str = "", today: date | None = None
) -> GeneratedDocument:
    """本文を組み立てる（保存はしない）。テストと画面プレビューの両方から呼ぶ。"""

    spec = spec_for(generator_key)

    if spec is None:
        raise ValueError(f"未知の生成種別です: {generator_key}")

    today = today or timezone.localdate()

    if generator_key == "meeting_minutes":
        return build_minutes(project, notes, today)

    if generator_key == "action_items":
        return build_action_items(project, notes, today)

    facts = collect_facts(project, today=today, period_start=period_start_for(generator_key, today))

    if generator_key == "incident_summary":
        return build_incident_summary(facts)

    if generator_key == "plan_draft":
        return build_plan_draft(facts)

    return build_report(facts, generator_key)


@transaction.atomic
def generate_and_save(
    *,
    project,
    generator_key: str,
    user=None,
    notes: str = "",
    today: date | None = None,
) -> GenerationResult:
    """生成して `Deliverable` へ保存する。

    本文は `ai_generated_body` にだけ入れ、`body`（確定本文）は空のままにする。
    ここで両方へ同じ文字列を入れてしまうと、人が 1 文字も直していないのに
    赤字率 0% と表示され、レビューを通したかどうかが区別できなくなる。
    """

    spec = spec_for(generator_key)

    if spec is None:
        return GenerationResult(ok=False, message="不明な生成種別です。")

    if spec.needs_notes and not notes.strip():
        return GenerationResult(ok=False, message="議事メモを入力してください。")

    document = build_document(project, generator_key, notes=notes, today=today)
    run = _record_run(project, spec, document, user=user, notes=notes)
    deliverable = Deliverable.objects.create(
        project=project,
        kind=document.deliverable_kind,
        title=document.title,
        version=_next_version(project, document.deliverable_kind),
        status=Deliverable.Status.DRAFT,
        ai_generated_body=document.body,
        body="",
        agent_run=run,
        created_by=user if getattr(user, "pk", None) else None,
    )

    if generator_key == "plan_draft" and document.has_material:
        _record_plan_draft(project, document, run)

    if not document.has_material:
        return GenerationResult(
            ok=True,
            message=f"{spec.label}を作成しましたが、材料になる実データがありません。",
            deliverable=deliverable,
            document=document,
        )

    return GenerationResult(
        ok=True,
        message=f"{spec.label}を生成しました。内容を確認して確定本文を保存してください。",
        deliverable=deliverable,
        document=document,
    )


def _next_version(project, kind: str) -> int:
    """同じ案件・同じ種別の中で版を繰り上げる。上書きせず履歴として残す。"""

    current = Deliverable.objects.filter(project=project, kind=kind).aggregate(
        latest=Max("version")
    )["latest"]

    return (current or 0) + 1


def _record_run(project, spec: GeneratorSpec, document: GeneratedDocument, user, notes: str):
    """生成の実行記録。根拠を `AgentStep` として 1 件ずつ残す。"""

    run = AgentRun.objects.create(
        tenant=project.tenant,
        project=project,
        user=user if getattr(user, "pk", None) else None,
        area=AgentRun.Area.DELIVERABLE,
        status=AgentRun.Status.SUCCEEDED,
        user_input=notes.strip() or f"{spec.label}を生成",
        intent=Intent.GENERAL,
        intent_confidence=1.0,
        plan=[f"{item.source}: {item.label}" for item in document.evidence],
    )

    AgentStep.objects.bulk_create(
        [
            AgentStep(
                run=run,
                order=order,
                tool_name=item.source[:64],
                status=AgentStep.Status.OK,
                input_summary=item.label,
                output_summary=item.detail,
            )
            for order, item in enumerate(document.evidence, start=1)
        ]
    )
    EvidenceEvaluation.objects.create(run=run, **_evaluation_kwargs(document))

    return run


def _evaluation_kwargs(document: GeneratedDocument) -> dict:
    """根拠評価。材料が無い生成物は承認へ進ませない。

    `Recommendation.ASK_CLARIFICATION` は `blocks_approval` が True になるので、
    「材料が無いのに承認されてしまう」経路をサーバ側で塞げる。
    """

    if not document.has_material:
        return {
            "confidence": 0.0,
            "relevance": Level.LOW,
            "coverage": Level.LOW,
            "recommendation": Recommendation.ASK_CLARIFICATION,
            "missing_information": list(document.warnings),
            "notes": "実データが無いため生成本文は雛形のみ。承認前に材料を登録すること。",
        }

    if document.warnings:
        return {
            "confidence": 0.6,
            "relevance": Level.HIGH,
            "coverage": Level.MEDIUM,
            "recommendation": Recommendation.ANSWER_WITH_CAUTION,
            "missing_information": list(document.warnings),
            "notes": "登録データから集計。注意事項を確認のうえ確定すること。",
        }

    return {
        "confidence": 0.9,
        "relevance": Level.HIGH,
        "coverage": Level.HIGH,
        "recommendation": Recommendation.ANSWER,
        "missing_information": [],
        "notes": "登録データのみから集計。推定値は含まない。",
    }


def _record_plan_draft(project, document: GeneratedDocument, run) -> PlanDraft:
    """計画ドラフトは一覧画面からも参照できるよう `PlanDraft` にも残す。"""

    return PlanDraft.objects.create(
        project=project,
        title=document.title,
        status=PlanDraft.Status.DRAFT,
        body=document.body,
        review_points=list(document.review_points),
        agent_run=run,
    )
