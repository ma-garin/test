"""ダッシュボードで人が入力する画面のフォーム。

AI の提案を「誰が・いつ・なぜ」採否したかを残すことが目的なので、
判断理由は空欄を許さない。理由のない判断はあとから検証できず、
根拠追跡という本システムの前提が崩れるため。
"""

from __future__ import annotations

from django import forms

from apps.dashboard.models import InterventionProposal

#: 人が選べる判断。提案中（proposed）へは戻せない。
DECIDABLE_STATUSES: tuple[str, ...] = (
    InterventionProposal.Status.ACCEPTED,
    InterventionProposal.Status.MODIFIED,
    InterventionProposal.Status.REJECTED,
    InterventionProposal.Status.DONE,
)


def decision_choices() -> list[tuple[str, str]]:
    """判断として選べる状態だけを返す。"""

    return [
        (value, label)
        for value, label in InterventionProposal.Status.choices
        if value in DECIDABLE_STATUSES
    ]


class InterventionDecisionForm(forms.ModelForm):
    """AI 介入提案に対する人の判断。

    対象の提案そのものは差し替えず（`instance` を渡さない）、検証済みの値だけを
    サービス層へ渡す。フォームがモデルを直接書き換えると、判断済みの提案を
    再判断できないという規則をビュー側で担保しきれなくなるため。
    """

    class Meta:
        model = InterventionProposal
        fields = ("status", "decision_reason", "modified_action")
        labels = {
            "status": "判断",
            "decision_reason": "判断理由（必須）",
            "modified_action": "修正後のアクション",
        }
        widgets = {
            "decision_reason": forms.Textarea(
                attrs={"rows": 3, "placeholder": "なぜその判断にしたかを残してください。"}
            ),
            "modified_action": forms.Textarea(
                attrs={"rows": 3, "placeholder": "「修正して採用」の場合、実際に行うアクションを書きます。"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["status"].choices = decision_choices()
        self.fields["status"].required = True
        self.fields["decision_reason"].required = True
        self.fields["modified_action"].required = False

    def clean_decision_reason(self) -> str:
        reason = (self.cleaned_data.get("decision_reason") or "").strip()

        if not reason:
            raise forms.ValidationError("判断理由は必ず入力してください。理由のない判断は記録しません。")

        return reason

    def clean(self):
        cleaned = super().clean()
        action = (cleaned.get("modified_action") or "").strip()

        if cleaned.get("status") == InterventionProposal.Status.MODIFIED and not action:
            self.add_error(
                "modified_action",
                "「修正して採用」では、修正後のアクション本文を残してください。",
            )

        cleaned["modified_action"] = action

        return cleaned
