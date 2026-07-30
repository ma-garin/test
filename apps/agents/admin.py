from django.contrib import admin

from apps.agents.models import AgentRun, AgentStep, EvidenceEvaluation, HumanReview


class AgentStepInline(admin.TabularInline):
    model = AgentStep
    extra = 0
    readonly_fields = ("order", "tool_name", "status", "input_summary", "output_summary", "elapsed_ms")


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ("created_at", "area", "intent", "status", "tenant", "project", "elapsed_ms")
    list_filter = ("area", "intent", "status", "tenant")
    search_fields = ("user_input",)
    inlines = [AgentStepInline]


@admin.register(EvidenceEvaluation)
class EvidenceEvaluationAdmin(admin.ModelAdmin):
    list_display = ("run", "confidence", "relevance", "coverage", "recommendation", "has_conflict")
    list_filter = ("recommendation", "relevance", "coverage", "has_conflict")


@admin.register(HumanReview)
class HumanReviewAdmin(admin.ModelAdmin):
    list_display = ("run", "reviewer", "decision", "reviewed_at")
    list_filter = ("decision",)
