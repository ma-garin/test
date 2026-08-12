"""LDF-06: Signal と案件データの関連付け。

誤った紐付けが一度予測へ入ると、もっともらしい誤報が連鎖する。そのため
`docs/改善に.md` 9 節の優先順位を、そのままコードの順序として持つ。

    1. 明示的ID一致   … 自動確定してよい
    2. 人が作った確定リンク … 既にあるので触らない
    3. 設定済み規則   … 候補。適用した規則を残す
    4. AI候補        … 候補。予測の確定根拠には使わない
    5. 未関連        … 削除せず残し、後で PMO が関連付け・除外できる

上位で決まったら下位は試さない。候補を積み増すほど、確認の手間が増えて
「未確認のまま放置」が起きるためである。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from apps.graph.models.graph import WorkLink
from apps.graph.ontology import LinkState, Provenance, RelationType
from apps.projects.models import Issue, WbsTask

#: 外部キーらしき文字列（PRJ-123、DEF-42 など）。
KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b")

#: WBS コードらしき文字列（3.2、4.1.2 など）。
WBS_PATTERN = re.compile(r"\b\d+(?:\.\d+){1,3}\b")

#: AI 候補として認める機能名の最小長。短すぎる名前は誤検出が多い。
MIN_FEATURE_NAME_LENGTH = 3


@dataclass(frozen=True)
class LinkProposal:
    """関連の候補 1 件。なぜそう判断したかを必ず持つ。"""

    target: object
    relation_type: str
    provenance: str
    state: str
    reason: str

    @property
    def is_auto_confirmable(self) -> bool:
        return self.state == LinkState.CONFIRMED


def propose_links(signal, *, features=(), rules=None) -> tuple[LinkProposal, ...]:
    """Signal から関連候補を作る。上位の根拠が見つかったら下位は試さない。"""

    text = f"{signal.summary} {signal.excerpt}"

    explicit = _by_external_key(signal.project, text)
    if explicit:
        return explicit

    by_wbs = _by_wbs_code(signal.project, text)
    if by_wbs:
        return by_wbs

    by_rule = _by_rule(signal, features, rules or {})
    if by_rule:
        return by_rule

    return _by_name(text, features)


def apply_proposals(signal, proposals, *, source_reference: str = "") -> tuple[WorkLink, ...]:
    """候補を関連として保存する。既にあるものは作り直さない。"""

    created: list[WorkLink] = []
    for proposal in proposals:
        exists = WorkLink.objects.filter(
            relation_type=proposal.relation_type,
            from_object_id=proposal.target.pk,
            to_object_id=signal.pk,
        ).exists()
        if exists:
            continue

        link = WorkLink(
            relation_type=proposal.relation_type,
            from_object=proposal.target,
            to_object=signal,
            provenance=proposal.provenance,
            state=proposal.state,
            source_reference=(source_reference or proposal.reason)[:300],
        )
        link.save()
        created.append(link)
    return tuple(created)


def _by_external_key(project, text: str) -> tuple[LinkProposal, ...]:
    """1. 明示的ID一致。外部キーが本文にあるなら自動確定してよい。"""

    keys = set(KEY_PATTERN.findall(text))
    if not keys:
        return ()

    # 外部キーを持つのは現状 `Issue` だけ。不具合に外部キーの列が増えたら
    # ここへ追記する。持っていないモデルを名称一致で代用しない。
    return tuple(
        LinkProposal(
            target=issue,
            relation_type=RelationType.DISCUSSED_IN,
            provenance=Provenance.EXTERNAL_ID,
            state=LinkState.CONFIRMED,
            reason=f"本文に外部キー {issue.external_key} が含まれています。",
        )
        for issue in Issue.objects.filter(project=project, external_key__in=keys)
    )


def _by_wbs_code(project, text: str) -> tuple[LinkProposal, ...]:
    """1'. WBS コードの一致。番号は誤検出しやすいので、存在するコードだけを採る。"""

    codes = set(WBS_PATTERN.findall(text))
    if not codes:
        return ()

    return tuple(
        LinkProposal(
            target=task,
            relation_type=RelationType.DISCUSSED_IN,
            provenance=Provenance.EXTERNAL_ID,
            state=LinkState.CONFIRMED,
            reason=f"本文に WBS {task.wbs_code} が含まれています。",
        )
        for task in WbsTask.objects.filter(project=project, wbs_code__in=codes)
    )


def _by_rule(signal, features, rules: dict) -> tuple[LinkProposal, ...]:
    """3. 設定済み規則。チャンネルと機能の固定対応など。候補にとどめる。"""

    feature_name = rules.get(signal.channel_reference)
    if not feature_name:
        return ()

    for feature in features:
        if feature.name == feature_name:
            return (
                LinkProposal(
                    target=feature,
                    relation_type=RelationType.DISCUSSED_IN,
                    provenance=Provenance.RULE,
                    state=LinkState.CANDIDATE,
                    reason=f"規則: チャンネル {signal.channel_reference} → 機能「{feature_name}」",
                ),
            )
    return ()


def _by_name(text: str, features) -> tuple[LinkProposal, ...]:
    """4. AI 候補相当の名称一致。確信度が低いので必ず候補にとどめる。"""

    return tuple(
        LinkProposal(
            target=feature,
            relation_type=RelationType.DISCUSSED_IN,
            provenance=Provenance.AI_CANDIDATE,
            state=LinkState.CANDIDATE,
            reason=f"本文に機能名「{feature.name}」が含まれています（未確認）。",
        )
        for feature in features
        if len(feature.name) >= MIN_FEATURE_NAME_LENGTH and feature.name in text
    )
