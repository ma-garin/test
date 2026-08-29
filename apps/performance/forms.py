"""計数・目標管理の入力フォーム。

参照範囲の絞り込みは `selectors` に集約しているが、フォーム側でも選択肢を
同じ範囲へ差し替える。ここを省くと、画面に出ていない組織へ POST で
書き込めてしまう（`projects.forms` と同じ理由）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django import forms

from apps.performance.constants import ImportKind, PlanKind, PlanStatus
from apps.performance.models import (
    FiscalYear,
    KpiDefinition,
    OrgMember,
    OrgUnit,
    PlanVersion,
)
from apps.performance.services.aggregation import Amounts
from apps.performance.services.calendar import format_month

#: グリッド1マスの入力欄。数値以外は弾き、空欄は「値なし」として通す。
MONEY_WIDGET = forms.NumberInput(attrs={"step": "1", "inputmode": "numeric"})


class FiscalYearForm(forms.ModelForm):
    class Meta:
        model = FiscalYear
        fields = ["code", "name", "start_on", "end_on", "is_current"]
        widgets = {
            "start_on": forms.DateInput(attrs={"type": "date"}),
            "end_on": forms.DateInput(attrs={"type": "date"}),
        }


class OrgUnitForm(forms.ModelForm):
    class Meta:
        model = OrgUnit
        fields = ["code", "name", "level", "parent", "manager", "project", "sort_order", "is_active"]

    def __init__(self, *args, tenant=None, units=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.fields["parent"].queryset = (
            units if units is not None else OrgUnit.objects.none()
        )
        self.fields["parent"].empty_label = "（上位組織なし＝部）"

        if tenant is not None:
            from apps.accounts.models import User
            from apps.projects.models import Project

            self.fields["manager"].queryset = User.objects.filter(tenant=tenant, is_active=True)
            self.fields["project"].queryset = Project.objects.alive().filter(tenant=tenant)

        self.fields["project"].empty_label = "（案件と対応づけない）"
        self.fields["manager"].empty_label = "（未設定）"

    def clean(self):
        cleaned = super().clean()

        # 自分自身や配下を上位組織にすると木が閉じる。編集時だけ起こりうる。
        parent = cleaned.get("parent")

        if parent is not None and self.instance.pk:
            cursor = parent

            while cursor is not None:
                if cursor.pk == self.instance.pk:
                    self.add_error("parent", "配下の組織を上位組織にはできません。")
                    break

                cursor = cursor.parent

        return cleaned


class OrgMemberForm(forms.ModelForm):
    class Meta:
        model = OrgMember
        fields = ["employee_code", "name", "org_unit", "title", "user", "is_active"]

    def __init__(self, *args, tenant=None, units=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.fields["org_unit"].queryset = units if units is not None else OrgUnit.objects.none()

        if tenant is not None:
            from apps.accounts.models import User

            self.fields["user"].queryset = User.objects.filter(tenant=tenant, is_active=True)

        self.fields["user"].empty_label = "（システム利用者と紐づけない）"


class PlanVersionForm(forms.ModelForm):
    """計画版の作成・編集。

    期初計画は年度に1本という制約があるため、既に存在する年度では種別を
    期中変更へ固定する。選べてしまうと保存時にデータベース制約で落ち、
    利用者には理由が分からないエラーになる。
    """

    class Meta:
        model = PlanVersion
        fields = ["kind", "name", "effective_from", "status", "note"]
        widgets = {
            "effective_from": forms.DateInput(attrs={"type": "date"}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, fiscal_year: FiscalYear, has_initial: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.fiscal_year = fiscal_year

        if has_initial and not (self.instance.pk and self.instance.is_initial):
            self.fields["kind"].choices = [(PlanKind.REVISED.value, PlanKind.REVISED.label)]
            self.fields["kind"].initial = PlanKind.REVISED
            self.fields["kind"].help_text = "期初計画は登録済みのため、期中変更計画として作成します。"

        self.fields["effective_from"].help_text = (
            f"この月から新しい計画が効きます（{format_month(fiscal_year.start_on)} 〜 "
            f"{format_month(fiscal_year.end_on)}）。月初日で保存されます。"
        )

    def clean_effective_from(self) -> date:
        value = self.cleaned_data["effective_from"].replace(day=1)

        if not self.fiscal_year.contains(value):
            raise forms.ValidationError("適用開始月は年度の範囲内にしてください。")

        return value

    def clean(self):
        cleaned = super().clean()

        if cleaned.get("kind") == PlanKind.INITIAL:
            # 期初計画は期首から効く。それ以外を許すと、期首月だけ計画が無い
            # 年度ができあがる。
            cleaned["effective_from"] = self.fiscal_year.start_on.replace(day=1)

        return cleaned


class KpiDefinitionForm(forms.ModelForm):
    class Meta:
        model = KpiDefinition
        fields = ["code", "name", "unit", "direction", "aggregation", "description", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class MonthlyFigureForm(forms.Form):
    """月次の計数グリッド。1行＝1か月、列は売上・粗利・利益。

    空欄と 0 を区別する。空欄は「値なし」で既存行を削除し、0 は「0 と置いた」
    として保存する（`services/entry` の約束と対）。
    """

    def __init__(self, *args, months: list[date], initial_amounts: dict | None = None, **kwargs):
        super().__init__(*args, **kwargs)

        self.months = months
        initial_amounts = initial_amounts or {}

        for month in months:
            amounts = initial_amounts.get(month)

            for name, label in (
                ("revenue", "売上"),
                ("gross_profit", "粗利"),
                ("operating_profit", "利益"),
            ):
                field = forms.DecimalField(
                    label=f"{format_month(month)} {label}",
                    required=False,
                    max_digits=15,
                    decimal_places=2,
                    widget=MONEY_WIDGET,
                )
                field.initial = getattr(amounts, name) if amounts is not None else None
                self.fields[self._key(month, name)] = field

    @staticmethod
    def _key(month: date, name: str) -> str:
        return f"m{month:%Y%m}_{name}"

    def rows(self):
        """テンプレート用。月と3つの入力欄を組にして返す。"""

        for month in self.months:
            yield {
                "month": month,
                "revenue": self[self._key(month, "revenue")],
                "gross_profit": self[self._key(month, "gross_profit")],
                "operating_profit": self[self._key(month, "operating_profit")],
            }

    def clean(self):
        cleaned = super().clean()

        for month in self.months:
            revenue = cleaned.get(self._key(month, "revenue"))
            gross = cleaned.get(self._key(month, "gross_profit"))
            profit = cleaned.get(self._key(month, "operating_profit"))

            # 粗利＞売上、利益＞粗利 は構造上ありえない。ここで止めないと
            # 利益率が 100% を超える行が集計に混ざり、原因の特定が難しくなる。
            if revenue is not None and gross is not None and gross > revenue:
                self.add_error(self._key(month, "gross_profit"), "粗利は売上を超えられません。")

            if gross is not None and profit is not None and profit > gross:
                self.add_error(self._key(month, "operating_profit"), "利益は粗利を超えられません。")

        return cleaned

    def amounts(self) -> dict:
        """月 → `Amounts`（3つとも空欄なら None＝削除）。"""

        result: dict = {}

        for month in self.months:
            values = [
                self.cleaned_data.get(self._key(month, name))
                for name in ("revenue", "gross_profit", "operating_profit")
            ]

            if all(value is None for value in values):
                result[month] = None
                continue

            result[month] = Amounts(
                revenue=values[0] if values[0] is not None else Decimal("0"),
                gross_profit=values[1] if values[1] is not None else Decimal("0"),
                operating_profit=values[2] if values[2] is not None else Decimal("0"),
            )

        return result


class KpiEntryForm(forms.Form):
    """KPI の目標値と月次実績をまとめて入力する。"""

    target_value = forms.DecimalField(
        label="目標値", required=False, max_digits=15, decimal_places=2, widget=MONEY_WIDGET
    )

    def __init__(
        self,
        *args,
        months: list[date],
        initial_target=None,
        initial_results: dict | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.months = months
        self.fields["target_value"].initial = initial_target
        initial_results = initial_results or {}

        for month in months:
            field = forms.DecimalField(
                label=format_month(month),
                required=False,
                max_digits=15,
                decimal_places=2,
                widget=MONEY_WIDGET,
            )
            field.initial = initial_results.get(month)
            self.fields[self._key(month)] = field

    @staticmethod
    def _key(month: date) -> str:
        return f"kpi_m{month:%Y%m}"

    def rows(self):
        for month in self.months:
            yield {"month": month, "field": self[self._key(month)]}

    def results(self) -> dict:
        return {month: self.cleaned_data.get(self._key(month)) for month in self.months}


class CsvImportForm(forms.Form):
    """CSV 取込の条件。年度・計画版はファイルではなくここで選ぶ。"""

    kind = forms.ChoiceField(label="取込種別", choices=ImportKind.choices)
    csv_file = forms.FileField(
        label="CSVファイル",
        help_text="UTF-8 または Shift_JIS。1行目は列名。",
    )
    fiscal_year = forms.ModelChoiceField(
        label="対象年度", queryset=FiscalYear.objects.none(), required=False
    )
    plan_version = forms.ModelChoiceField(
        label="対象計画版", queryset=PlanVersion.objects.none(), required=False
    )
    skip_errors = forms.BooleanField(
        label="エラー行を除いて取り込む",
        required=False,
        help_text="既定ではエラーが1行でもあれば何も取り込みません。",
    )
    overwrite_manual = forms.BooleanField(
        label="手入力の値も上書きする",
        required=False,
        help_text="既定では画面から手入力した値を CSV で上書きしません。",
    )

    #: 種別ごとに要る条件。ここを1か所に持ち、検証と画面表示で共有する。
    NEEDS_YEAR = (ImportKind.ACTUAL_FIGURE, ImportKind.KPI_RESULT)
    NEEDS_VERSION = (ImportKind.PLAN_FIGURE, ImportKind.KPI_TARGET)

    def __init__(self, *args, tenant=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        if tenant is not None:
            self.fields["fiscal_year"].queryset = FiscalYear.objects.filter(tenant=tenant)
            self.fields["plan_version"].queryset = PlanVersion.objects.filter(
                tenant=tenant
            ).exclude(status=PlanStatus.ARCHIVED)

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")

        if kind in self.NEEDS_YEAR and not cleaned.get("fiscal_year"):
            self.add_error("fiscal_year", "この種別では対象年度が必要です。")

        if kind in self.NEEDS_VERSION and not cleaned.get("plan_version"):
            self.add_error("plan_version", "この種別では対象の計画版が必要です。")

        version = cleaned.get("plan_version")

        if version is not None and not cleaned.get("fiscal_year"):
            # 版が決まれば年度も決まる。利用者に二重入力させない。
            cleaned["fiscal_year"] = version.fiscal_year

        return cleaned
