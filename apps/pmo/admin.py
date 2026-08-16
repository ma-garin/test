from django.contrib import admin

from apps.pmo.models import Approval, Consultation, Deliverable, PlanDraft, PromptTemplate


@admin.register(PromptTemplate)
class PromptTemplateAdmin(admin.ModelAdmin):
    list_display = ("title", "tenant", "category", "intent", "is_active")
    list_filter = ("tenant", "category", "is_active")
    search_fields = ("title", "body")


@admin.register(Deliverable)
class DeliverableAdmin(admin.ModelAdmin):
    list_display = ("title", "version", "project", "kind", "status", "created_by")
    list_filter = ("project", "kind", "status")
    search_fields = ("title",)


@admin.register(Approval)
class ApprovalAdmin(admin.ModelAdmin):
    list_display = ("deliverable", "decision", "actor", "created_at")
    list_filter = ("decision",)


admin.site.register([Consultation, PlanDraft])
