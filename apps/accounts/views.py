"""ログインとテナント切替。"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from apps.accounts.models import Tenant
from apps.core.middleware import TENANT_SESSION_KEY


@login_required
def select_tenant(request: HttpRequest) -> HttpResponse:
    """テナント切替。自分の所属テナント以外は選べない。"""

    if request.user.is_superuser:
        tenants = Tenant.objects.filter(is_active=True)
    else:
        tenants = Tenant.objects.filter(pk=request.user.tenant_id, is_active=True)

    if request.method == "POST":
        tenant_id = request.POST.get("tenant")

        if tenants.filter(pk=tenant_id).exists():
            request.session[TENANT_SESSION_KEY] = str(tenant_id)

            return redirect("dashboard:control")

    return render(
        request,
        "pages/select_tenant.html",
        {"tenants": tenants, "page_title": "テナント選択"},
    )
