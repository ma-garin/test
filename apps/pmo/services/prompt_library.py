"""プロンプトライブラリ。

テナントに `PromptTemplate` が登録されていれば DB を正とし、未登録なら
ここの既定セットを出す。新任が最初に開いた時点で空白の画面を見せないための措置で、
モデルを増やさずに初期値を持たせる方法として定数を採っている。
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.pmo.selectors import prompt_templates_for


@dataclass(frozen=True)
class PromptEntry:
    """画面へ渡すテンプレート 1 件。DB 由来か既定かを問わず同じ形にする。"""

    key: str
    title: str
    category: str
    description: str
    body: str
    intent: str = ""
    is_builtin: bool = False


#: 既定テンプレート。PMO の代表的な 6 タスクに対応させている。
DEFAULT_ENTRIES: tuple[PromptEntry, ...] = (
    PromptEntry(
        key="delay-triage",
        title="進捗遅延の整理",
        category="進捗管理",
        description="遅延の事実・原因・影響・打ち手を分けて整理させる。",
        body="以下の遅延について、事実／推定原因／後続工程への影響／取りうる打ち手を分けて整理してください。\n対象: ",
        intent="delay",
        is_builtin=True,
    ),
    PromptEntry(
        key="risk-review",
        title="リスクの棚卸し",
        category="リスク管理",
        description="発生確率と影響度を分けて評価し、監視指標まで出させる。",
        body="現在のリスク一覧について、発生確率・影響度・兆候となる監視指標・一次対応を整理してください。\n観点: ",
        intent="risk",
        is_builtin=True,
    ),
    PromptEntry(
        key="issue-escalation",
        title="課題のエスカレーション判断",
        category="課題管理",
        description="上位者へ上げるべきかの判断材料をそろえる。",
        body="次の課題について、エスカレーションの要否と、その判断に必要な根拠を整理してください。\n課題: ",
        intent="issue",
        is_builtin=True,
    ),
    PromptEntry(
        key="quality-report",
        title="品質レポートの下書き",
        category="品質管理",
        description="不具合傾向と是正措置を報告様式で書かせる。",
        body="今回のテスト結果から、不具合の傾向・重大度別の件数・是正措置案を含む品質レポートの下書きを作成してください。\n対象期間: ",
        intent="quality",
        is_builtin=True,
    ),
    PromptEntry(
        key="change-impact",
        title="変更影響の評価",
        category="変更管理",
        description="スコープ・スケジュール・コストへの影響を分けて出させる。",
        body="次の変更要求について、スコープ・スケジュール・コスト・品質への影響を分けて評価してください。\n変更内容: ",
        intent="change",
        is_builtin=True,
    ),
    PromptEntry(
        key="weekly-report",
        title="週次報告の骨子",
        category="報告",
        description="実績・予定・課題・依頼事項の 4 区分で下書きさせる。",
        body="今週の状況から、実績／来週の予定／課題とリスク／関係者への依頼事項の 4 区分で週次報告の骨子を作成してください。\n補足: ",
        intent="general",
        is_builtin=True,
    ),
)


def entries_for(tenant) -> list[PromptEntry]:
    """テナントのテンプレート一覧。未登録なら既定セットを返す。"""

    registered = [
        PromptEntry(
            key=template.key,
            title=template.title,
            category=template.category or "未分類",
            description=template.description,
            body=template.body,
            intent=template.intent,
        )
        for template in prompt_templates_for(tenant)
    ]

    return registered or list(DEFAULT_ENTRIES)


def categories(entries: list[PromptEntry]) -> list[str]:
    """カテゴリの表示順。登録順を保ったまま重複を除く。"""

    seen: list[str] = []

    for entry in entries:
        if entry.category not in seen:
            seen.append(entry.category)

    return seen
