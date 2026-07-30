"""案件配下データの入力フォーム。

テナント分離は selectors に集約しているため、フォーム側でも「参照できる案件」
以外を選べないよう queryset を差し替える。ここを省くと、画面上は見えない案件に
POST でタスクを差し込めてしまう。
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.db.models import QuerySet

from apps.projects.models import ChangeRequest, Defect, Issue, Project, Risk, WbsTask

MIN_PROGRESS = Decimal("0")
MAX_PROGRESS = Decimal("100")


class WbsTaskForm(forms.ModelForm):
    """WBS タスクの作成・編集フォーム。"""

    class Meta:
        model = WbsTask
        fields = [
            "project",
            "wbs_code",
            "name",
            "owner",
            "planned_start",
            "planned_end",
            "progress_percent",
            "priority",
            "status",
            "follow_up_state",
            "next_action",
            "ball_holder",
            "evidence_note",
        ]
        widgets = {
            "planned_start": forms.DateInput(attrs={"type": "date"}),
            "planned_end": forms.DateInput(attrs={"type": "date"}),
            "evidence_note": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, projects: QuerySet[Project] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # 参照できない案件を選択肢から外す。None は「絞り込み不能」なので空にする。
        self.fields["project"].queryset = (
            projects if projects is not None else Project.objects.none()
        )
        self.fields["project"].empty_label = "案件を選択"

    def clean_progress_percent(self) -> Decimal:
        value = self.cleaned_data["progress_percent"]

        if value is None:
            return Decimal("0")

        if value < MIN_PROGRESS or value > MAX_PROGRESS:
            raise forms.ValidationError("進捗率は 0〜100 の範囲で入力してください。")

        return value

    def clean(self):
        cleaned = super().clean()
        project = cleaned.get("project")
        wbs_code = cleaned.get("wbs_code")

        if project is None or not wbs_code:
            return cleaned

        duplicated = WbsTask.objects.filter(project=project, wbs_code=wbs_code)

        if self.instance.pk is not None:
            duplicated = duplicated.exclude(pk=self.instance.pk)

        if duplicated.exists():
            self.add_error("wbs_code", "この案件には同じ WBS 番号のタスクが既にあります。")

        return cleaned


SCALE_MIN = 1
SCALE_MAX = 5

_DATE_WIDGET = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def _validate_scale(value: int | None, label: str) -> int | None:
    """1〜5 の範囲を強制する。範囲外はスコア（影響度 × 発生確率）の意味を壊す。"""

    if value is None:
        return value

    if value < SCALE_MIN or value > SCALE_MAX:
        raise forms.ValidationError(f"{label}は {SCALE_MIN}〜{SCALE_MAX} で入力してください。")

    return value


class ProjectScopedForm(forms.ModelForm):
    """案件選択肢を参照可能な案件だけに差し替えるフォームの基底。"""

    def __init__(self, *args, projects: QuerySet[Project] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.fields["project"].queryset = (
            projects if projects is not None else Project.objects.none()
        )
        self.fields["project"].empty_label = "案件を選択"


class RiskForm(ProjectScopedForm):
    """リスクの作成・編集フォーム。"""

    class Meta:
        model = Risk
        fields = [
            "project",
            "title",
            "description",
            "status",
            "impact",
            "probability",
            "mitigation",
            "owner",
            "due_date",
        ]
        widgets = {
            "due_date": _DATE_WIDGET,
            "description": forms.Textarea(attrs={"rows": 3}),
            "mitigation": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_impact(self) -> int | None:
        return _validate_scale(self.cleaned_data.get("impact"), "影響度")

    def clean_probability(self) -> int | None:
        return _validate_scale(self.cleaned_data.get("probability"), "発生確率")


class IssueForm(ProjectScopedForm):
    """課題の作成・編集フォーム。"""

    class Meta:
        model = Issue
        fields = [
            "project",
            "title",
            "description",
            "status",
            "severity",
            "owner",
            "due_date",
            "external_key",
        ]
        widgets = {
            "due_date": _DATE_WIDGET,
            "description": forms.Textarea(attrs={"rows": 3}),
        }


DECIDED_CHANGE_STATUSES = (ChangeRequest.Status.APPROVED, ChangeRequest.Status.REJECTED)
EDITABLE_CHANGE_STATUSES = (
    ChangeRequest.Status.DRAFT,
    ChangeRequest.Status.UNDER_REVIEW,
    ChangeRequest.Status.PENDING_APPROVAL,
)


class ChangeRequestForm(ProjectScopedForm):
    """変更要求の作成・編集フォーム。判断（承認・却下）はここでは行わせない。"""

    impact_scope = forms.CharField(
        label="影響範囲",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="影響を受ける機能・工程・成果物を1行に1件で入力します。",
    )

    class Meta:
        model = ChangeRequest
        fields = [
            "project",
            "title",
            "status",
            "requested_by",
            "description",
            "impact_summary",
            "impact_scope",
            "affected_tasks",
            "estimated_effort_days",
            "schedule_impact_days",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "impact_summary": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, projects: QuerySet[Project] | None = None, **kwargs) -> None:
        super().__init__(*args, projects=projects, **kwargs)

        # 影響タスクも参照できる案件のものだけに限定する。
        self.fields["affected_tasks"].queryset = (
            WbsTask.objects.filter(project__in=projects)
            if projects is not None
            else WbsTask.objects.none()
        )

        instance = self.instance

        if instance.pk and instance.status in DECIDED_CHANGE_STATUSES:
            # 判断済みの状態は判断画面でしか動かさない（証跡と状態を食い違わせない）。
            self.fields.pop("status")
        else:
            self.fields["status"].choices = [
                (value, label)
                for value, label in ChangeRequest.Status.choices
                if value in EDITABLE_CHANGE_STATUSES
            ]

        if instance.pk:
            self.initial["impact_scope"] = "\n".join(instance.impact_scope or [])

    def clean_impact_scope(self) -> list[str]:
        raw = self.cleaned_data.get("impact_scope") or ""

        return [line.strip() for line in raw.splitlines() if line.strip()]


class ChangeDecisionForm(forms.Form):
    """変更要求の判断。監査対象のため理由を必須にする。"""

    APPROVE = "approved"
    REJECT = "rejected"
    DECISION_CHOICES = ((APPROVE, "承認する"), (REJECT, "却下する"))

    decision = forms.ChoiceField(label="判断", choices=DECISION_CHOICES, widget=forms.RadioSelect)
    reason = forms.CharField(
        label="判断理由",
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="誰がいつ何を理由に決めたかを残すため、理由は必須です。",
    )

    def clean_reason(self) -> str:
        reason = (self.cleaned_data.get("reason") or "").strip()

        if not reason:
            raise forms.ValidationError("判断理由を入力してください。")

        return reason


class DefectForm(ProjectScopedForm):
    """不具合の作成・編集フォーム。"""

    class Meta:
        model = Defect
        fields = [
            "project",
            "title",
            "status",
            "severity",
            "phase",
            "description",
            "detected_on",
            "closed_on",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "detected_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "closed_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


class RiskPromoteForm(forms.ModelForm):
    """リスク顕在化時に起票する課題。案件はリスクから引き継ぐため選ばせない。"""

    class Meta:
        model = Issue
        fields = ["title", "description", "severity", "owner", "due_date", "external_key"]
        widgets = {
            "due_date": _DATE_WIDGET,
            "description": forms.Textarea(attrs={"rows": 3}),
        }
