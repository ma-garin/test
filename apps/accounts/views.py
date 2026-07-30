"""ログインとテナント切替。"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.forms import EmailLoginForm
from apps.accounts.models import Tenant
from apps.core.middleware import TENANT_SESSION_KEY


def login_view(request: HttpRequest) -> HttpResponse:
    """メールアドレスだけでログインする。

    パスワードを検証せず、未登録のアドレスは利用者を作って通す。
    弾かれるのは形式不正と、無効化済みの利用者だけ。
    """

    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    form = EmailLoginForm(request.POST or None)
    error = ""

    if request.method == "POST" and form.is_valid():
        user = authenticate(request, email=form.cleaned_data["email"])

        if user is None:
            error = "このメールアドレスではログインできません。"
        else:
            login(request, user)

            return redirect(_safe_next(request) or settings.LOGIN_REDIRECT_URL)

    return render(
        request,
        "pages/login.html",
        {"form": form, "error": error, "page_title": "ログイン"},
    )


def _safe_next(request: HttpRequest) -> str:
    """`next` は自ホスト宛てのときだけ採用する。"""

    target = request.POST.get("next") or request.GET.get("next") or ""

    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target

    return ""


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
