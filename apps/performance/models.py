"""組織別・個人別の計数（売上・粗利・利益）と目標／KPI を管理するテーブル。

**このアプリが引き受ける範囲**

ラインマネージャーが自部門の計数を見る単位は「部 → 課 → プロジェクト」の
組織階層で、組織マスタ（`OrgUnit`）として独立に持つ。

**設計上の約束**

- 金額は年月（月初日）単位で持つ。年度計は月次の合計として導出し、
  重複して保存しない。年計と月計を両方保存すると必ず食い違う。
- 利益率は保存しない。売上・粗利・利益の3つだけを保存し、率は導出する。
  率を保存すると、金額を直したのに率が古いままという状態が作れてしまう。
- 期初計画は上書きしない。見直しは期中変更計画として別バージョンで積む。
- 組織の値と個人の値は別レコードで持ち、集計時に足し合わせない
  （個人は組織値の内訳とみなす）。詳細は `services/aggregation.py`。
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import SoftDeleteModel, TimeStampedModel
from apps.performance.constants import (
    ORG_LEVEL_DEPTH,
    FigureSource,
    ImportKind,
    ImportStatus,
    KpiAggregation,
    KpiDirection,
    OrgLevel,
    PlanKind,
    PlanStatus,
)

#: 金額の桁。円単位で兆の桁まで置けるようにする。
MONEY_DIGITS = 15
MONEY_PLACES = 2

ZERO = Decimal("0")


def money_field(label: str) -> models.DecimalField:
    return models.DecimalField(
        label,
        max_digits=MONEY_DIGITS,
        decimal_places=MONEY_PLACES,
        default=ZERO,
    )


class OrgUnit(SoftDeleteModel):
    """部・課・プロジェクトの組織ノード。

    親子は自己参照で持つ。閉包テーブルを置かないのは、1テナントの組織数が
    数百に収まる規模で、木の走査を Python 側でやっても実測で問題にならないため
    （`selectors.org_tree`）。件数が桁で増えたら経路の実体化を検討する。
    """

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="org_units",
    )
    parent = models.ForeignKey(
        "self",
        verbose_name="上位組織",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )
    level = models.CharField("階層", max_length=16, choices=OrgLevel.choices)
    code = models.SlugField("組織コード", max_length=64)
    name = models.CharField("組織名", max_length=200)
    manager = models.ForeignKey(
        "accounts.User",
        verbose_name="ラインマネージャー",
        on_delete=models.SET_NULL,
        related_name="managed_org_units",
        null=True,
        blank=True,
        help_text="この組織と配下の計数を編集できる利用者。",
    )
    sort_order = models.PositiveSmallIntegerField("表示順", default=100)
    is_active = models.BooleanField("有効", default=True)

    class Meta:
        verbose_name = "組織"
        verbose_name_plural = "組織"
        ordering = ["level", "sort_order", "code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uniq_org_unit_code_per_tenant"),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"

    def clean(self) -> None:
        super().clean()

        if self.parent_id is not None:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "自分自身を上位組織にはできません。"})

            if self.parent.tenant_id != self.tenant_id:
                raise ValidationError({"parent": "他テナントの組織は上位組織にできません。"})

            # 階層を飛ばした登録（部の直下にプロジェクト）は集計の解釈が割れるので止める。
            if ORG_LEVEL_DEPTH[self.level] != ORG_LEVEL_DEPTH[self.parent.level] + 1:
                raise ValidationError(
                    {"parent": f"{self.get_level_display()}の上位は"
                     f"{self._expected_parent_label()}にしてください。"}
                )
        elif ORG_LEVEL_DEPTH[self.level] != 0:
            raise ValidationError({"parent": "上位組織を指定してください。"})

    def _expected_parent_label(self) -> str:
        depth = ORG_LEVEL_DEPTH[self.level] - 1

        for value, label in OrgLevel.choices:
            if ORG_LEVEL_DEPTH[value] == depth:
                return label

        return "上位組織"


class OrgMember(TimeStampedModel):
    """組織に所属する個人。

    全員が本システムの利用者とは限らない（計数の対象ではあるがログインしない）。
    そのため `user` は任意で、同定は社員番号で行う。
    """

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="org_members",
    )
    org_unit = models.ForeignKey(
        OrgUnit,
        verbose_name="所属組織",
        on_delete=models.PROTECT,
        related_name="members",
    )
    employee_code = models.CharField("社員番号", max_length=64)
    name = models.CharField("氏名", max_length=120)
    user = models.ForeignKey(
        "accounts.User",
        verbose_name="利用者",
        on_delete=models.SET_NULL,
        related_name="org_memberships",
        null=True,
        blank=True,
    )
    title = models.CharField("役割", max_length=120, blank=True)
    is_active = models.BooleanField("在籍", default=True)

    class Meta:
        verbose_name = "メンバー"
        verbose_name_plural = "メンバー"
        ordering = ["employee_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "employee_code"], name="uniq_org_member_code_per_tenant"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_code} {self.name}"


class FiscalYear(TimeStampedModel):
    """会計年度。月次の枠を決める。"""

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="fiscal_years",
    )
    code = models.SlugField("年度コード", max_length=32)
    name = models.CharField("年度名", max_length=120)
    start_on = models.DateField("期首")
    end_on = models.DateField("期末")
    is_current = models.BooleanField("今期", default=False)

    class Meta:
        verbose_name = "年度"
        verbose_name_plural = "年度"
        ordering = ["-start_on"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uniq_fiscal_year_per_tenant"),
        ]

    def __str__(self) -> str:
        return self.name or self.code

    def clean(self) -> None:
        super().clean()

        if self.start_on and self.end_on and self.end_on <= self.start_on:
            raise ValidationError({"end_on": "期末は期首より後にしてください。"})

    @property
    def months(self) -> list:
        """期首から期末までの月初日。計数はこの並びで持つ。"""

        from apps.performance.services.calendar import months_between

        return months_between(self.start_on, self.end_on)

    @property
    def previous(self) -> FiscalYear | None:
        """前年同期比較に使う、1年前の年度。

        年度コード（FY2026 → FY2025）の命名規則には依存しない。コードが
        規則的でない運用でも前年比較が壊れないよう、期首日を1年ずらして
        一致する年度を探す。見つからなければ None（前年比は「未確認」扱い）。
        """

        from apps.performance.services.calendar import shift_year

        target = shift_year(self.start_on, -1)

        return (
            FiscalYear.objects.filter(tenant_id=self.tenant_id, start_on=target)
            .exclude(pk=self.pk)
            .first()
        )

    def contains(self, month) -> bool:
        return bool(month and self.start_on <= month <= self.end_on)


class PlanVersion(TimeStampedModel):
    """計数計画の版。期初計画1本と、期中変更計画 n 本。

    `effective_from` は「この版が実力値として効き始める月」。期中変更を
    月の途中から適用したいという要望は必ず出るが、計数は月次でしか持たない
    ため、適用単位も月に丸める。
    """

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="plan_versions",
    )
    fiscal_year = models.ForeignKey(
        FiscalYear,
        verbose_name="年度",
        on_delete=models.CASCADE,
        related_name="plan_versions",
    )
    kind = models.CharField("種別", max_length=16, choices=PlanKind.choices)
    revision = models.PositiveSmallIntegerField(
        "改訂番号",
        default=0,
        help_text="期初計画は 0。期中変更は 1 から順に振る。",
    )
    name = models.CharField("計画名", max_length=200, blank=True)
    effective_from = models.DateField("適用開始月")
    status = models.CharField(
        "状態", max_length=16, choices=PlanStatus.choices, default=PlanStatus.DRAFT
    )
    note = models.TextField("変更理由", blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        verbose_name="作成者",
        on_delete=models.SET_NULL,
        related_name="created_plan_versions",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "計数計画"
        verbose_name_plural = "計数計画"
        ordering = ["fiscal_year", "revision"]
        constraints = [
            models.UniqueConstraint(
                fields=["fiscal_year", "kind", "revision"], name="uniq_plan_version_revision"
            ),
            models.UniqueConstraint(
                fields=["fiscal_year"],
                condition=Q(kind=PlanKind.INITIAL),
                name="uniq_initial_plan_per_year",
            ),
        ]

    def __str__(self) -> str:
        return self.name or self.default_name

    @property
    def default_name(self) -> str:
        if self.kind == PlanKind.INITIAL:
            return "期初計画"

        return f"期中変更計画 第{self.revision}次"

    @property
    def is_initial(self) -> bool:
        return self.kind == PlanKind.INITIAL


class FigureAmountsMixin(models.Model):
    """計数3項目と、そこから導出する率。

    率を持たせるのはモデル側の責務にする。テンプレートで割り算を書くと
    ゼロ除算の扱いが画面ごとにばらつき、売上0の組織で 0% と「—」が混在する。
    """

    revenue = money_field("売上")
    gross_profit = money_field("粗利")
    operating_profit = money_field("利益")

    class Meta:
        abstract = True

    @property
    def gross_margin_rate(self) -> Decimal | None:
        return _rate(self.gross_profit, self.revenue)

    @property
    def profit_rate(self) -> Decimal | None:
        return _rate(self.operating_profit, self.revenue)


def _rate(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """百分率。売上が0なら率は定義できないので None を返す（0% ではない）。"""

    if not denominator:
        return None

    return (Decimal(numerator) / Decimal(denominator) * 100).quantize(Decimal("0.01"))


class PlanFigure(FigureAmountsMixin, TimeStampedModel):
    """計画の月次計数。`member` が空なら組織レベルの計画。"""

    plan_version = models.ForeignKey(
        PlanVersion,
        verbose_name="計画版",
        on_delete=models.CASCADE,
        related_name="figures",
    )
    org_unit = models.ForeignKey(
        OrgUnit,
        verbose_name="組織",
        on_delete=models.CASCADE,
        related_name="plan_figures",
    )
    member = models.ForeignKey(
        OrgMember,
        verbose_name="メンバー",
        on_delete=models.CASCADE,
        related_name="plan_figures",
        null=True,
        blank=True,
    )
    month = models.DateField("対象月", help_text="月初日で持つ。")
    source = models.CharField(
        "入力経路", max_length=16, choices=FigureSource.choices, default=FigureSource.MANUAL
    )
    note = models.CharField("備考", max_length=255, blank=True)

    class Meta:
        verbose_name = "計画計数"
        verbose_name_plural = "計画計数"
        ordering = ["month", "org_unit"]
        constraints = [
            # NULL 同士は等しくない扱いになるため、組織レベルと個人レベルで
            # 制約を分ける。1本にまとめると組織レベルの重複を止められない。
            models.UniqueConstraint(
                fields=["plan_version", "org_unit", "month"],
                condition=Q(member__isnull=True),
                name="uniq_plan_figure_org_month",
            ),
            models.UniqueConstraint(
                fields=["plan_version", "org_unit", "member", "month"],
                condition=Q(member__isnull=False),
                name="uniq_plan_figure_member_month",
            ),
        ]
        indexes = [models.Index(fields=["plan_version", "month"])]

    def __str__(self) -> str:
        return f"{self.org_unit.code} {self.month:%Y-%m} 計画"


class ActualFigure(FigureAmountsMixin, TimeStampedModel):
    """実績の月次計数。計画版には紐づかない（実績は1本しかない）。"""

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="actual_figures",
    )
    fiscal_year = models.ForeignKey(
        FiscalYear,
        verbose_name="年度",
        on_delete=models.CASCADE,
        related_name="actual_figures",
    )
    org_unit = models.ForeignKey(
        OrgUnit,
        verbose_name="組織",
        on_delete=models.CASCADE,
        related_name="actual_figures",
    )
    member = models.ForeignKey(
        OrgMember,
        verbose_name="メンバー",
        on_delete=models.CASCADE,
        related_name="actual_figures",
        null=True,
        blank=True,
    )
    month = models.DateField("対象月")
    source = models.CharField(
        "入力経路", max_length=16, choices=FigureSource.choices, default=FigureSource.MANUAL
    )
    note = models.CharField("備考", max_length=255, blank=True)
    updated_by = models.ForeignKey(
        "accounts.User",
        verbose_name="最終更新者",
        on_delete=models.SET_NULL,
        related_name="updated_actual_figures",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "実績計数"
        verbose_name_plural = "実績計数"
        ordering = ["month", "org_unit"]
        constraints = [
            models.UniqueConstraint(
                fields=["fiscal_year", "org_unit", "month"],
                condition=Q(member__isnull=True),
                name="uniq_actual_figure_org_month",
            ),
            models.UniqueConstraint(
                fields=["fiscal_year", "org_unit", "member", "month"],
                condition=Q(member__isnull=False),
                name="uniq_actual_figure_member_month",
            ),
        ]
        indexes = [models.Index(fields=["fiscal_year", "month"])]

    def __str__(self) -> str:
        return f"{self.org_unit.code} {self.month:%Y-%m} 実績"


class KpiDefinition(TimeStampedModel):
    """KPI の定義。目標値・実績値は別テーブルで年度・組織ごとに持つ。"""

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="kpi_definitions",
    )
    code = models.SlugField("KPIコード", max_length=64)
    name = models.CharField("KPI名", max_length=200)
    unit = models.CharField("単位", max_length=32, blank=True)
    direction = models.CharField(
        "評価方向", max_length=16, choices=KpiDirection.choices, default=KpiDirection.UP
    )
    aggregation = models.CharField(
        "年度集計方法",
        max_length=16,
        choices=KpiAggregation.choices,
        default=KpiAggregation.SUM,
        help_text="月次実績から年度実績を出す方法。件数は合計、率は平均、残高は最新値。",
    )
    description = models.TextField("定義・算出方法", blank=True)
    is_active = models.BooleanField("有効", default=True)

    class Meta:
        verbose_name = "KPI"
        verbose_name_plural = "KPI"
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uniq_kpi_code_per_tenant"),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"

    @property
    def higher_is_better(self) -> bool:
        return self.direction == KpiDirection.UP


class KpiTarget(TimeStampedModel):
    """KPI の目標値。計画版に紐づけ、期中変更でも目標を持ち直せるようにする。"""

    kpi = models.ForeignKey(
        KpiDefinition,
        verbose_name="KPI",
        on_delete=models.CASCADE,
        related_name="targets",
    )
    plan_version = models.ForeignKey(
        PlanVersion,
        verbose_name="計画版",
        on_delete=models.CASCADE,
        related_name="kpi_targets",
    )
    org_unit = models.ForeignKey(
        OrgUnit,
        verbose_name="組織",
        on_delete=models.CASCADE,
        related_name="kpi_targets",
    )
    member = models.ForeignKey(
        OrgMember,
        verbose_name="メンバー",
        on_delete=models.CASCADE,
        related_name="kpi_targets",
        null=True,
        blank=True,
    )
    target_value = models.DecimalField("目標値", max_digits=15, decimal_places=2)
    note = models.CharField("備考", max_length=255, blank=True)

    class Meta:
        verbose_name = "KPI目標"
        verbose_name_plural = "KPI目標"
        ordering = ["kpi", "org_unit"]
        constraints = [
            models.UniqueConstraint(
                fields=["kpi", "plan_version", "org_unit"],
                condition=Q(member__isnull=True),
                name="uniq_kpi_target_org",
            ),
            models.UniqueConstraint(
                fields=["kpi", "plan_version", "org_unit", "member"],
                condition=Q(member__isnull=False),
                name="uniq_kpi_target_member",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kpi.code} {self.org_unit.code} 目標"


class KpiResult(TimeStampedModel):
    """KPI の月次実績。"""

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="kpi_results",
    )
    kpi = models.ForeignKey(
        KpiDefinition,
        verbose_name="KPI",
        on_delete=models.CASCADE,
        related_name="results",
    )
    fiscal_year = models.ForeignKey(
        FiscalYear,
        verbose_name="年度",
        on_delete=models.CASCADE,
        related_name="kpi_results",
    )
    org_unit = models.ForeignKey(
        OrgUnit,
        verbose_name="組織",
        on_delete=models.CASCADE,
        related_name="kpi_results",
    )
    member = models.ForeignKey(
        OrgMember,
        verbose_name="メンバー",
        on_delete=models.CASCADE,
        related_name="kpi_results",
        null=True,
        blank=True,
    )
    month = models.DateField("対象月")
    actual_value = models.DecimalField("実績値", max_digits=15, decimal_places=2)
    source = models.CharField(
        "入力経路", max_length=16, choices=FigureSource.choices, default=FigureSource.MANUAL
    )
    note = models.CharField("備考", max_length=255, blank=True)

    class Meta:
        verbose_name = "KPI実績"
        verbose_name_plural = "KPI実績"
        ordering = ["month", "kpi"]
        constraints = [
            models.UniqueConstraint(
                fields=["kpi", "org_unit", "month"],
                condition=Q(member__isnull=True),
                name="uniq_kpi_result_org_month",
            ),
            models.UniqueConstraint(
                fields=["kpi", "org_unit", "member", "month"],
                condition=Q(member__isnull=False),
                name="uniq_kpi_result_member_month",
            ),
        ]
        indexes = [models.Index(fields=["fiscal_year", "month"])]

    def __str__(self) -> str:
        return f"{self.kpi.code} {self.month:%Y-%m} 実績"


class ImportBatch(TimeStampedModel):
    """CSV 取込の履歴。

    取込は「何行入って、何行はねられ、なぜはねられたか」が後から追えないと
    運用に乗らない。エラーは行番号付きで JSON に残す。
    """

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="performance_imports",
    )
    kind = models.CharField("取込種別", max_length=32, choices=ImportKind.choices)
    filename = models.CharField("ファイル名", max_length=255, blank=True)
    status = models.CharField("結果", max_length=16, choices=ImportStatus.choices)
    row_count = models.PositiveIntegerField("データ行数", default=0)
    created_count = models.PositiveIntegerField("新規", default=0)
    updated_count = models.PositiveIntegerField("更新", default=0)
    error_count = models.PositiveIntegerField("エラー", default=0)
    errors = models.JSONField("エラー明細", default=list, blank=True)
    context = models.JSONField("取込条件", default=dict, blank=True)
    uploaded_by = models.ForeignKey(
        "accounts.User",
        verbose_name="実行者",
        on_delete=models.SET_NULL,
        related_name="performance_imports",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "CSV取込"
        verbose_name_plural = "CSV取込"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def is_clean(self) -> bool:
        return self.error_count == 0
