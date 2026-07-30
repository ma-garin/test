from django.contrib import admin

from apps.audit.models import Feedback, OperationLog


@admin.register(OperationLog)
class OperationLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "target", "succeeded", "user", "project")
    list_filter = ("tenant", "action", "succeeded")
    search_fields = ("action", "target")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("created_at", "rating", "has_fact_error", "user", "answer", "agent_run")
    list_filter = ("tenant", "rating", "has_fact_error")
