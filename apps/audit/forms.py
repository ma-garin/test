"""フィードバック投稿フォーム。

対象（RAG 回答 / Agentic 実行）の選択肢は、必ず自テナントのものだけに絞る。
選択肢を絞らないと、ID を直接送るだけで他テナントのレコードへ評価を
ぶら下げられてしまうため。
"""

from __future__ import annotations

from django import forms

from apps.agents.models import AgentRun
from apps.audit.models import Feedback
from apps.rag.models import RagAnswer

#: 選択肢に出す上限。監査画面なので直近だけ選べれば足りる。
TARGET_LIMIT = 50


class FeedbackForm(forms.ModelForm):
    """AI の回答に対する評価。

    「役に立たなかった」「事実誤認あり」のときはコメントを必須にする。
    改善に使えない否定評価だけが溜まるのを避けるため。
    """

    class Meta:
        model = Feedback
        fields = ("answer", "agent_run", "rating", "has_fact_error", "comment")
        labels = {
            "answer": "対象のRAG回答",
            "agent_run": "対象のAgentic実行",
            "rating": "評価",
            "has_fact_error": "事実誤認があった",
            "comment": "コメント",
        }
        widgets = {
            "comment": forms.Textarea(
                attrs={"rows": 4, "placeholder": "どこが役に立った／立たなかったかを具体的に書いてください。"}
            ),
        }

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["answer"].queryset = _answers_for(tenant)
        self.fields["agent_run"].queryset = _runs_for(tenant)
        self.fields["answer"].required = False
        self.fields["agent_run"].required = False
        self.fields["answer"].empty_label = "（指定しない）"
        self.fields["agent_run"].empty_label = "（指定しない）"
        self.fields["comment"].required = False

    def clean_comment(self) -> str:
        return (self.cleaned_data.get("comment") or "").strip()

    def clean(self):
        cleaned = super().clean()
        rating = cleaned.get("rating")
        needs_comment = rating == Feedback.Rating.BAD or cleaned.get("has_fact_error")

        if needs_comment and not cleaned.get("comment"):
            self.add_error(
                "comment",
                "否定評価・事実誤認ありの場合は、何が問題だったかをコメントに残してください。",
            )

        return cleaned


def _answers_for(tenant):
    """自テナントの RAG 回答。テナント未確定なら空。"""

    if tenant is None:
        return RagAnswer.objects.none()

    visible = RagAnswer.objects.filter(query__tenant=tenant).select_related("query")

    return visible.filter(pk__in=visible.values("pk")[:TARGET_LIMIT])


def _runs_for(tenant):
    """自テナントの Agentic 実行。テナント未確定なら空。"""

    if tenant is None:
        return AgentRun.objects.none()

    visible = AgentRun.objects.filter(tenant=tenant)

    return visible.filter(pk__in=visible.values("pk")[:TARGET_LIMIT])
