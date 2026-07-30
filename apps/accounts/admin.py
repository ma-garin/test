from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.accounts.models import Tenant, User


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "display_name", "tenant", "role", "is_active")
    list_filter = ("role", "tenant", "is_active", "is_staff")
    search_fields = ("username", "display_name", "email")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("PMO Agent", {"fields": ("tenant", "display_name", "role")}),
    )
