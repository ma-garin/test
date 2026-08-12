from django.contrib import admin

from apps.projects.models import (
    ChangeRequest,
    Defect,
    Issue,
    Milestone,
    Project,
    ProjectMember,
    QualityMetric,
    Risk,
    WbsTask,
)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "tenant", "status", "rag_status", "progress_percent", "is_demo")
    list_filter = ("tenant", "status", "rag_status", "is_demo")
    search_fields = ("code", "name")


@admin.register(WbsTask)
class WbsTaskAdmin(admin.ModelAdmin):
    list_display = ("wbs_code", "name", "project", "status", "planned_end", "actual_end", "is_critical_path")
    list_filter = ("project", "status", "is_critical_path")
    search_fields = ("wbs_code", "name")


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "status", "severity", "owner", "due_date")
    list_filter = ("project", "status", "severity")
    search_fields = ("title",)


@admin.register(Risk)
class RiskAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "status", "probability", "impact", "owner")
    list_filter = ("project", "status")
    search_fields = ("title",)


@admin.register(Defect)
class DefectAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "status", "severity", "phase", "detected_on")
    list_filter = ("project", "status", "severity")
    search_fields = ("title",)


@admin.register(ChangeRequest)
class ChangeRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "status", "requested_by", "estimated_effort_days")
    list_filter = ("project", "status")
    search_fields = ("title",)


admin.site.register([Milestone, ProjectMember, QualityMetric])
