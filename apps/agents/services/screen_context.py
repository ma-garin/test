"""開いている画面の文脈（REQ: 開いている画面情報の自動読込）。

相談画面へ移動した時点で「どの画面から来たか」「何を見ていたか」が失われると、
利用者は毎回「リスク一覧で、案件Aの……」と状況説明から書き直すことになる。
各画面のテンプレートが `?screen=...&subject=...` を付けて相談へ渡し、
ここで画面定義と突き合わせて確認観点まで復元する。

**LLM は使わない。** 画面ごとの確認観点は業務上あらかじめ決まっており、
推論させる必要がない。定義表として持つほうが説明可能で再現する。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScreenDefinition:
    """画面ひとつぶんの定義。"""

    key: str
    label: str
    #: その画面で PMO が最初に確かめるべきこと。相談の初期観点として提示する。
    viewpoints: tuple[str, ...]
    #: `subject` が何を指すかの説明（画面に出す）。
    subject_label: str = "対象"


@dataclass(frozen=True)
class ScreenContext:
    """実際に開いていた画面と対象。"""

    key: str
    label: str
    subject: str
    viewpoints: tuple[str, ...]
    subject_label: str = "対象"

    @property
    def headline(self) -> str:
        """回答の冒頭に置く「〜画面の〜について」。"""

        if self.subject:
            return f"{self.label}の{self.subject}について"

        return f"{self.label}について"

    def decorate(self, question: str) -> str:
        """相談本文へ画面文脈を付ける。

        `AgentRun.user_input` へそのまま保存する。後からトレースを見た人が
        「何を見ながらの相談か」を復元できないと、判断の妥当性を検証できない。
        """

        text = (question or "").strip()

        return f"［{self.headline}］\n{text}" if text else f"［{self.headline}］"

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "subject": self.subject,
            "viewpoints": list(self.viewpoints),
        }


#: 画面ごとの確認観点。キーはテンプレートが渡す `screen` の値。
SCREENS: dict[str, ScreenDefinition] = {
    definition.key: definition
    for definition in (
        ScreenDefinition(
            key="project_detail",
            label="案件詳細画面",
            subject_label="案件",
            viewpoints=("進捗の実績と計画の差", "未解決の課題と担当", "高スコアのリスク", "直近の変更要求"),
        ),
        ScreenDefinition(
            key="risk_list",
            label="リスク一覧画面",
            subject_label="案件",
            viewpoints=("対策の有無", "対応期限", "影響度と発生確率", "リスク化の要否", "エスカレーション要否"),
        ),
        ScreenDefinition(
            key="issue_list",
            label="課題一覧画面",
            subject_label="案件",
            viewpoints=("優先度", "担当の割当", "対応期限の超過", "滞留期間", "エスカレーション要否"),
        ),
        ScreenDefinition(
            key="task_detail",
            label="WBSタスク詳細画面",
            subject_label="タスク",
            viewpoints=("遅延の有無", "ボール保持者", "後続工程への影響", "ブロック要因", "次アクション"),
        ),
        ScreenDefinition(
            key="task_list",
            label="WBSタスク一覧画面",
            subject_label="案件",
            viewpoints=("期限超過の件数", "ブロック中のタスク", "更新が止まっているタスク", "クリティカルパス"),
        ),
        ScreenDefinition(
            key="defect_list",
            label="不具合一覧画面",
            subject_label="案件",
            viewpoints=("重大度の分布", "未解決件数", "検出工程の偏り", "収束傾向", "再発防止"),
        ),
        ScreenDefinition(
            key="change_list",
            label="変更影響画面",
            subject_label="案件",
            viewpoints=("スコープ影響", "承認状況", "工数影響", "テスト範囲", "関係者合意"),
        ),
        ScreenDefinition(
            key="quality",
            label="品質状況画面",
            subject_label="案件",
            viewpoints=("テスト消化率", "不具合収束", "品質ゲートの充足", "残リスク"),
        ),
        ScreenDefinition(
            key="progress",
            label="進捗管理画面",
            subject_label="案件",
            viewpoints=("計画と実績の差", "遅延工程", "リカバリ策", "後続工程影響"),
        ),
        ScreenDefinition(
            key="deliverables",
            label="成果物生成画面",
            subject_label="成果物",
            viewpoints=("根拠の十分性", "赤字率", "事実誤認の有無", "承認可否"),
        ),
        ScreenDefinition(
            key="approvals",
            label="承認フロー画面",
            subject_label="成果物",
            viewpoints=("承認をブロックしている理由", "不足している根拠", "差し戻し要否"),
        ),
        ScreenDefinition(
            key="control_dashboard",
            label="管制ダッシュボード画面",
            subject_label="案件",
            viewpoints=("ヘルススコアの内訳", "重要アラート", "対応の優先順位"),
        ),
        ScreenDefinition(
            key="detection_list",
            label="予兆検知画面",
            subject_label="案件",
            viewpoints=("検知理由", "根拠データ", "誤検知の可能性", "先行日数"),
        ),
    )
}

#: 定義に無い画面キーが来たときの受け皿。相談自体は止めない。
FALLBACK = ScreenDefinition(
    key="unknown",
    label="現在の画面",
    subject_label="対象",
    viewpoints=("状況整理", "不足情報", "次アクション"),
)


def resolve(screen: str | None, subject: str | None = None) -> ScreenContext | None:
    """画面キーと対象名から画面文脈を組み立てる。

    画面キーが空なら None（相談画面を直接開いた場合）。未知のキーは
    握りつぶさず `FALLBACK` で受け、対象名だけでも文脈として残す。
    """

    key = (screen or "").strip()

    if not key:
        return None

    definition = SCREENS.get(key, FALLBACK)
    # 対象名は画面から来る自由文字列。長すぎる値をそのまま保存すると
    # トレースが読めなくなるため、ここで切り詰める。
    cleaned_subject = (subject or "").strip()[:120]

    return ScreenContext(
        key=key if key in SCREENS else FALLBACK.key,
        label=definition.label,
        subject=cleaned_subject,
        viewpoints=definition.viewpoints,
        subject_label=definition.subject_label,
    )


def viewpoints_for(screen: str | None) -> tuple[str, ...]:
    """画面の確認観点だけを取り出す。相談前の初期提示に使う。"""

    context = resolve(screen)

    return context.viewpoints if context is not None else ()
