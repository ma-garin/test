"""LDF-07: テスト結果を Feature と不具合へ結び付ける。

「テスト失敗・修正・再試験の事実が Feature と不具合へ結び付く」ことが受入条件。
ただし結び付け方には優先順位がある（`apps.forecast.services.linking` と同じ考え方）。

- 不具合の外部キーが結果に入っている → 確定してよい
- 機能名の手掛かりしかない → 候補にとどめる（人が確認するまで予測に使わない）
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.forecast.models.evidence import TestEvidence
from apps.forecast.models.signals import Signal, SignalClassification, SignalSource
from apps.graph.models.graph import Feature, WorkLink
from apps.graph.ontology import LinkState, Provenance, RelationType
from apps.integrations.services.connectors.test_evidence import ExternalTestResult
from apps.projects.models import Issue


@dataclass(frozen=True)
class TestBridgeResult:
    """取り込み結果。確定と候補を分けて数える。"""

    created: int = 0
    updated: int = 0
    confirmed_links: int = 0
    candidate_links: int = 0
    rejected: tuple[str, ...] = ()

    def summary_line(self) -> str:
        return (
            f"証跡 新規 {self.created}件 / 更新 {self.updated}件 / "
            f"確定リンク {self.confirmed_links}件 / 候補リンク {self.candidate_links}件 / "
            f"契約違反 {len(self.rejected)}件"
        )


def ingest_test_results(project, results) -> TestBridgeResult:
    """コネクタが返した正規形を、証跡・Signal・関連として取り込む。"""

    features = list(Feature.objects.filter(project=project))
    created = updated = confirmed = candidates = 0
    rejected: list[str] = []

    for result in results:
        try:
            result.validate()
        except ValueError as error:
            rejected.append(f"{result.external_id}: {error}")
            continue

        evidence, was_created = _upsert_evidence(project, result)
        created, updated = (created + 1, updated) if was_created else (created, updated + 1)

        signal = _signal_for(project, evidence, result)
        confirmed += _link_defect(project, signal, result)
        candidates += _link_feature(project, signal, evidence, features, result)

    return TestBridgeResult(
        created=created,
        updated=updated,
        confirmed_links=confirmed,
        candidate_links=candidates,
        rejected=tuple(rejected),
    )


def _upsert_evidence(project, result: ExternalTestResult) -> tuple[TestEvidence, bool]:
    return TestEvidence.objects.update_or_create(
        project=project,
        external_id=result.external_id,
        defaults={
            "name": result.name,
            "kind": result.kind,
            "result": result.result,
            "executed_at": result.executed_at,
            "environment": result.environment,
            "failure_reason": result.failure_reason[:300],
            "external_url": result.url,
            "retest_planned_on": result.retest_planned_on,
            "defect_reference": result.defect_reference,
            "origin": TestEvidence.Origin.CONNECTOR,
        },
    )


def _signal_for(project, evidence: TestEvidence, result: ExternalTestResult) -> Signal:
    classification = (
        SignalClassification.TEST_FAILED
        if evidence.is_failure
        else SignalClassification.TEST_PASSED
    )
    payload_hash = Signal.compute_hash(
        SignalSource.TEST_MANAGEMENT,
        result.external_id,
        result.result,
        result.executed_at.isoformat(),
    )
    signal, _ = Signal.objects.update_or_create(
        project=project,
        source=SignalSource.TEST_MANAGEMENT,
        payload_hash=payload_hash,
        defaults={
            "external_id": f"{result.external_id}@{result.executed_at.isoformat()}",
            "classification": classification,
            "occurred_at": result.executed_at,
            "summary": f"{result.name}: {evidence.get_result_display()}"[:300],
            "permalink": result.url,
            "excerpt": result.failure_reason[:300],
        },
    )
    return signal


def _link_defect(project, signal, result: ExternalTestResult) -> int:
    """外部キーの一致は自動確定してよい。"""

    if not result.defect_reference:
        return 0

    issue = Issue.objects.filter(project=project, external_key=result.defect_reference).first()
    if issue is None:
        return 0

    return _ensure_link(
        source=issue,
        target=signal,
        relation=RelationType.EVIDENCED_BY,
        provenance=Provenance.EXTERNAL_ID,
        state=LinkState.CONFIRMED,
        reason=f"テスト結果に外部キー {result.defect_reference} が含まれています。",
    )


def _link_feature(project, signal, evidence, features, result: ExternalTestResult) -> int:
    """機能名の手掛かりは候補にとどめる。確認するまで予測へ効かせない。"""

    hint = result.feature_hint or result.name
    created = 0
    for feature in features:
        if feature.name not in hint:
            continue
        created += _ensure_link(
            source=feature,
            target=signal,
            relation=RelationType.EVIDENCED_BY,
            provenance=Provenance.AI_CANDIDATE,
            state=LinkState.CANDIDATE,
            reason=f"テスト名に機能名「{feature.name}」が含まれています（未確認）。",
        )
        if evidence.feature_id is None:
            evidence.feature = feature
            evidence.save(update_fields=["feature", "updated_at"])
    return created


def _ensure_link(*, source, target, relation, provenance, state, reason) -> int:
    exists = WorkLink.objects.filter(
        relation_type=relation, from_object_id=source.pk, to_object_id=target.pk
    ).exists()
    if exists:
        return 0

    link = WorkLink(
        relation_type=relation,
        from_object=source,
        to_object=target,
        provenance=provenance,
        state=state,
        source_reference=reason[:300],
    )
    link.save()
    return 1
