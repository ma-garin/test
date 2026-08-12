"""検知の実行制御。

検知そのものは各モジュールが行い、ここは横断的な 3 つだけを担う。

1. 重複排除 … 同じ対象に未対応のアラートが残っていれば作らない
2. 件数制限 … 1 回の実行で作るアラート数に上限を設ける
3. 記録     … Alert と InterventionProposal を根拠つきで保存する

`dry_run=True` なら一切保存しない。画面の「検知結果」は毎回この乾式実行で
描画するので、押す前に何が作られるかを確認できる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.dashboard.models import Alert, InterventionProposal
from apps.dashboard.services.detection import (
    change_frequency,
    critical_path,
    defect_rate,
    silent_fire,
)
from apps.dashboard.services.detection.findings import (
    Finding,
    Skip,
    SkipReason,
    kind_label,
)
from apps.dashboard.services.detection.proposals import build_proposals
from apps.dashboard.services.detection.rules import max_alerts_per_run
from apps.projects.models import Project

#: 重要度の優先順位。上限で打ち切るとき、重いものを先に残す。
_SEVERITY_ORDER = {
    Alert.Severity.CRITICAL: 0,
    Alert.Severity.WARNING: 1,
    Alert.Severity.INFO: 2,
}

#: 既に人の手が入っている＝重複とみなすアラート状態。
#: 解消・対象外まで進んだものは再検知してよい（同じ問題が再発した可能性がある）。
ACTIVE_ALERT_STATUSES = (Alert.Status.OPEN, Alert.Status.ACKNOWLEDGED)


@dataclass
class DetectionResult:
    """1 回の実行結果。作った物と、作らなかった理由の両方を持つ。"""

    dry_run: bool = False
    findings: list[Finding] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)
    created_alerts: list[Alert] = field(default_factory=list)
    created_proposals: list[InterventionProposal] = field(default_factory=list)
    project_count: int = 0

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def alert_count(self) -> int:
        return len(self.created_alerts)

    @property
    def proposal_count(self) -> int:
        return len(self.created_proposals)

    @property
    def undetermined(self) -> list[Skip]:
        """母数不足で判定できなかったもの。安全と混同させない。"""

        return [skip for skip in self.skips if skip.is_undetermined]

    @property
    def duplicate_count(self) -> int:
        return len([s for s in self.skips if s.reason == SkipReason.DUPLICATE])

    def summary_line(self) -> str:
        """CLI とメッセージで使う 1 行要約。"""

        prefix = "（乾式実行）" if self.dry_run else ""

        return (
            f"{prefix}案件 {self.project_count}件 を検査: 検知 {self.finding_count}件 / "
            f"アラート {self.alert_count}件 / 介入提案 {self.proposal_count}件 / "
            f"見送り {len(self.skips)}件（うち判定不能 {len(self.undetermined)}件）"
        )


def run_detection(
    projects: QuerySet[Project] | list[Project],
    *,
    now: datetime | None = None,
    today: date | None = None,
    dry_run: bool = False,
) -> DetectionResult:
    """対象案件を検査し、検知結果を返す（必要なら保存する）。

    `projects` はテナント分離済みの QuerySet を渡すこと。ここでは絞り込まない。
    """

    now = now or timezone.now()
    today = today or timezone.localdate()
    target_projects = list(projects)
    result = DetectionResult(dry_run=dry_run, project_count=len(target_projects))

    for project in target_projects:
        for findings, skips in _run_detectors(project, now=now, today=today):
            result.findings.extend(findings)
            result.skips.extend(skips)

    kept, blocked = _filter_findings(result.findings, target_projects)
    result.skips.extend(blocked)
    # 見送った分は skips 側に理由が残る。findings は「実際に採用した検知」だけにする。
    result.findings = kept

    if not dry_run:
        _persist(result, kept, now=now)

    return result


def _run_detectors(project: Project, *, now: datetime, today: date):
    """検知器を順に呼ぶ。1 つが落ちても他を止めないよう、呼び出しはここに集約する。"""

    yield critical_path.detect(project, today=today)
    yield silent_fire.detect(project, today=today)
    yield change_frequency.detect(project, now=now)
    yield defect_rate.detect(project, today=today)


def _filter_findings(
    findings: list[Finding], projects: list[Project]
) -> tuple[list[Finding], list[Skip]]:
    """重複と件数上限で絞る。落としたものは理由つきの Skip にする。"""

    existing = _active_dedupe_keys(projects)
    limit = max_alerts_per_run()
    ordered = sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.project.code, f.dedupe_key),
    )

    kept: list[Finding] = []
    blocked: list[Skip] = []
    seen: set[tuple] = set()

    for finding in ordered:
        key = (finding.project.pk, finding.dedupe_key)

        if key in existing or key in seen:
            blocked.append(
                Skip(
                    finding.project,
                    finding.kind,
                    SkipReason.DUPLICATE,
                    f"{kind_label(finding.kind)}: 同じ対象（{finding.dedupe_key}）に"
                    "未対応のアラートが残っているため作成しません。",
                )
            )
            continue

        if len(kept) >= limit:
            blocked.append(
                Skip(
                    finding.project,
                    finding.kind,
                    SkipReason.LIMIT_REACHED,
                    f"1回あたりの上限 {limit}件 に達したため見送りました: {finding.title}",
                )
            )
            continue

        seen.add(key)
        kept.append(finding)

    return kept, blocked


def _active_dedupe_keys(projects: list[Project]) -> set[tuple]:
    """未対応・確認済みのアラートが持つ重複判定鍵。

    JSON 検索はバックエンド差が出るため、Python 側で組み立てる。
    対象は未クローズのアラートだけなので件数は限られる。
    """

    alerts = Alert.objects.filter(
        project__in=projects, status__in=ACTIVE_ALERT_STATUSES
    ).values_list("project_id", "evidence")
    keys: set[tuple] = set()

    for project_id, evidence in alerts:
        if isinstance(evidence, dict) and evidence.get("dedupe_key"):
            keys.add((project_id, evidence["dedupe_key"]))

    return keys


def _persist(result: DetectionResult, findings: list[Finding], *, now: datetime) -> None:
    """アラートと介入提案を保存する。片方だけ残らないよう 1 トランザクションにする。"""

    with transaction.atomic():
        for finding in findings:
            evidence = dict(finding.evidence)
            evidence["dedupe_key"] = finding.dedupe_key
            evidence["detected_by"] = "rule_based"

            alert = Alert.objects.create(
                project=finding.project,
                category=finding.category,
                severity=finding.severity,
                status=Alert.Status.OPEN,
                title=finding.title[:300],
                detail=finding.detail,
                detected_at=now,
                evidence=evidence,
            )
            result.created_alerts.append(alert)

            proposals = build_proposals(finding, alert=alert)

            for proposal in proposals:
                proposal.save()

            result.created_proposals.extend(proposals)
