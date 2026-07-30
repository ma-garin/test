"""#66 検知結果から介入提案を自動生成する。

提案は「案が 1 つだけ」だと人が選べない。PMO が現場で取り得る選択肢は
おおむね「人を足す」「範囲を削る」「期限を動かす」に集約されるため、
検知種別ごとに複数案のテンプレートを持ち、根拠を添えて並べる。

LLM は使わない（ADR-0003）。テンプレート＋検知根拠の差し込みだけで成立させ、
LLM が使える環境では文面の質が上がる、という位置づけにする。
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.dashboard.models import InterventionProposal
from apps.dashboard.services.detection.findings import Finding
from apps.dashboard.services.detection.rules import max_proposals_per_finding


@dataclass(frozen=True)
class ProposalTemplate:
    """1 つの打ち手。title は提案名、action は最初の一歩。"""

    title: str
    action: str
    expected_effect: str


#: 検知種別ごとの打ち手。順序が優先順位を表す。
TEMPLATES: dict[str, tuple[ProposalTemplate, ...]] = {
    "critical_path": (
        ProposalTemplate(
            "要員追加でクリティカルパスを回復する",
            "遅延タスクへ経験者を1名増員し、後続の着手予定日を維持する",
            "後続工程の開始遅れを防ぎ、全体スケジュールへの波及を止める",
        ),
        ProposalTemplate(
            "範囲を縮小して後続の依存を切る",
            "遅延タスクの成果物を必須部分に絞り、後続が着手できる形で先行リリースする",
            "増員なしで後続の待ちを解消する。削った範囲は次フェーズへ送る",
        ),
        ProposalTemplate(
            "後続工程の期限を関係者と再交渉する",
            "波及先の期限を実績ベースで引き直し、顧客・関連部署と合意する",
            "実現不能な計画を残さず、以降の予実差を意味のある数字に戻す",
        ),
    ),
    "silent_fire": (
        ProposalTemplate(
            "ボールの所在を確認し、担当を明示し直す",
            "ボール保持者へ状況を確認し、次アクションと期日をタスクへ記録する",
            "止まっている理由が可視化され、放置が続かなくなる",
        ),
        ProposalTemplate(
            "PMOフォロー状態を引き上げて監視下に置く",
            "対象タスクのフォロー状態を「フォロー中」へ変更し、週次で確認する",
            "表面化していない停滞を、定例の議題として扱えるようにする",
        ),
        ProposalTemplate(
            "エスカレーションして意思決定を求める",
            "停滞の原因が案件内で解けない場合、上位へ判断を上げる",
            "判断待ちによる停滞を、期限付きの意思決定へ変える",
        ),
    ),
    "change_frequency": (
        ProposalTemplate(
            "変更管理会議の頻度を上げて滞留を防ぐ",
            "影響分析中の変更要求を棚卸しし、承認判断を週次から隔日へ切り替える",
            "判断待ちの変更要求が減り、実装の手戻りを抑える",
        ),
        ProposalTemplate(
            "要件の凍結範囲を合意する",
            "以降のフェーズで受け付ける変更の条件を顧客と文書で合意する",
            "変更の流入そのものを抑え、計画の前提を安定させる",
        ),
        ProposalTemplate(
            "変更による追加工数を再見積もりする",
            "直近の変更要求の影響工数を集計し、計画へ反映する",
            "変更を織り込んだ現実的な計画に戻す",
        ),
    ),
    "defect_rate": (
        ProposalTemplate(
            "重大不具合の原因分析を先に行う",
            "重大度の高い不具合を対象に、混入工程別の原因分析を実施する",
            "同種の不具合の再発を止め、以降の発生率を下げる",
        ),
        ProposalTemplate(
            "品質ゲートの通過条件を引き上げる",
            "次工程への移行条件へ「重大不具合ゼロ」を追加する",
            "未収束のまま次工程へ進むことを防ぐ",
        ),
        ProposalTemplate(
            "修正体制を増強して滞留を解消する",
            "未クローズの不具合へ担当を割り当て直し、修正のリードタイムを短縮する",
            "滞留している不具合が減り、収束曲線が想定へ戻る",
        ),
    ),
}


def build_proposals(finding: Finding, *, alert=None) -> list[InterventionProposal]:
    """検知 1 件に対する介入提案（未保存）を作る。

    提案理由には必ず検知根拠をそのまま入れる。根拠の無い提案は採用されず、
    出し続けると提案そのものが読まれなくなる。
    """

    templates = TEMPLATES.get(finding.kind, ())[: max_proposals_per_finding()]
    evidence = [
        {
            "type": "detection",
            "rule": finding.kind,
            "dedupe_key": finding.dedupe_key,
            "reason": finding.reason,
            "observed": finding.evidence.get("observed", {}),
            "threshold": finding.evidence.get("threshold", {}),
        }
    ]

    return [
        InterventionProposal(
            project=finding.project,
            alert=alert,
            title=template.title,
            rationale=f"{finding.title}\n検知根拠: {finding.reason}",
            evidence=evidence,
            # ルールベース検知なので信頼度は付けない（AI 由来と区別するため）。
            confidence=None,
            recommended_action=template.action,
            expected_effect=template.expected_effect,
        )
        for template in templates
    ]
