"""成果物生成・編集のフォーム。

案件の選択肢は必ず呼び出し側から渡された queryset に差し替える。ここを省くと、
画面に出ていない案件へ POST で成果物を差し込めてしまう（テナント分離の穴になる）。
"""

from __future__ import annotations

from django import forms
from django.db.models import QuerySet

from apps.pmo.models import Deliverable
from apps.pmo.services.generators import generator_choices, spec_for
from apps.projects.models import Project


class DeliverableGenerateForm(forms.Form):
    """成果物を生成するフォーム。"""

    project = forms.ModelChoiceField(
        label="案件",
        queryset=None,
        empty_label=None,
    )
    generator = forms.ChoiceField(label="生成する成果物", choices=generator_choices)
    notes = forms.CharField(
        label="議事メモ（議事録要約・ToDo抽出のときだけ使用）",
        required=False,
        widget=forms.Textarea(attrs={"rows": 8, "placeholder": "決定: 〜\nTODO: 〜\n→ 〜"}),
    )

    def __init__(self, *args, projects: QuerySet | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # queryset を渡し忘れたときに「全案件が選べる」状態にならないよう、
        # 既定は空にする。テナント分離の失敗を安全側へ倒すため。
        self.fields["project"].queryset = (
            projects if projects is not None else Project.objects.none()
        )

    def clean(self):
        """議事メモが必須の種別で空入力なら、生成前に止める。"""

        cleaned = super().clean()
        spec = spec_for(cleaned.get("generator") or "")

        if spec and spec.needs_notes and not (cleaned.get("notes") or "").strip():
            self.add_error("notes", "この種別では議事メモの入力が必要です。")

        return cleaned


class DeliverableEditForm(forms.ModelForm):
    """確定本文の編集フォーム。

    編集できるのはタイトルと確定本文だけ。`ai_generated_body` を編集させると
    赤字率の基準（AI が最初に何を書いたか）が失われるため、画面から触らせない。
    """

    class Meta:
        model = Deliverable
        fields = ["title", "body"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 20}),
        }
        labels = {"title": "タイトル", "body": "確定本文（人が編集）"}

    def clean_body(self) -> str:
        body = self.cleaned_data.get("body", "")

        if not body.strip():
            raise forms.ValidationError("確定本文が空です。AI生成本文を編集して保存してください。")

        return body
