from django.contrib import admin

from apps.documents.models import Document, DocumentPage, IngestJob, Template, TemplateOutput


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "tenant", "project", "file_type", "status", "last_indexed_at")
    list_filter = ("tenant", "status", "file_type")
    search_fields = ("title", "sha256")
    readonly_fields = ("sha256", "file_size")


@admin.register(IngestJob)
class IngestJobAdmin(admin.ModelAdmin):
    list_display = ("job_type", "status", "document", "started_at", "finished_at")
    list_filter = ("job_type", "status", "tenant")


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "mapping_status")
    list_filter = ("tenant", "mapping_status")
    search_fields = ("name", "keywords")


admin.site.register([DocumentPage, TemplateOutput])
