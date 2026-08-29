from django.contrib import admin

from apps.performance.models import (
    ActualFigure,
    FiscalYear,
    ImportBatch,
    KpiDefinition,
    KpiResult,
    KpiTarget,
    OrgMember,
    OrgUnit,
    PlanFigure,
    PlanVersion,
)


@admin.register(OrgUnit)
class OrgUnitAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "level", "parent", "manager", "tenant")
    list_filter = ("tenant", "level", "is_active")
    search_fields = ("code", "name")


@admin.register(OrgMember)
class OrgMemberAdmin(admin.ModelAdmin):
    list_display = ("employee_code", "name", "org_unit", "is_active")
    list_filter = ("tenant", "is_active")
    search_fields = ("employee_code", "name")


@admin.register(FiscalYear)
class FiscalYearAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "start_on", "end_on", "is_current", "tenant")
    list_filter = ("tenant", "is_current")


@admin.register(PlanVersion)
class PlanVersionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "fiscal_year", "kind", "revision", "effective_from", "status")
    list_filter = ("tenant", "kind", "status")


@admin.register(PlanFigure)
class PlanFigureAdmin(admin.ModelAdmin):
    list_display = ("plan_version", "org_unit", "member", "month", "revenue", "operating_profit")
    list_filter = ("plan_version", "month")


@admin.register(ActualFigure)
class ActualFigureAdmin(admin.ModelAdmin):
    list_display = ("fiscal_year", "org_unit", "member", "month", "revenue", "operating_profit")
    list_filter = ("fiscal_year", "month", "source")


@admin.register(KpiDefinition)
class KpiDefinitionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "unit", "direction", "aggregation", "is_active")
    list_filter = ("tenant", "direction", "is_active")


@admin.register(KpiTarget)
class KpiTargetAdmin(admin.ModelAdmin):
    list_display = ("kpi", "plan_version", "org_unit", "member", "target_value")


@admin.register(KpiResult)
class KpiResultAdmin(admin.ModelAdmin):
    list_display = ("kpi", "fiscal_year", "org_unit", "member", "month", "actual_value")
    list_filter = ("fiscal_year", "month", "source")


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("kind", "filename", "status", "row_count", "error_count", "created_at")
    list_filter = ("tenant", "kind", "status")
