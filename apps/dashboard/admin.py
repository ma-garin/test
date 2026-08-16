from django.contrib import admin

from apps.dashboard.models import Alert, HealthSnapshot, InterventionProposal, KpiMeasurement


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "category", "severity", "status", "detected_at")
    list_filter = ("project", "category", "severity", "status")
    search_fields = ("title",)


@admin.register(InterventionProposal)
class InterventionProposalAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "status", "confidence", "decided_by", "decided_at")
    list_filter = ("project", "status")
    search_fields = ("title",)


@admin.register(KpiMeasurement)
class KpiMeasurementAdmin(admin.ModelAdmin):
    list_display = ("kind", "project", "measured_on", "baseline_value", "actual_value", "target_value")
    list_filter = ("project", "kind")


admin.site.register([HealthSnapshot])
