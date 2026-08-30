"""ログインとテナント切替。"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.db.models import Count
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

            # next が明示されているとき（認証が必要なページへの直リンク等）は、
            # オンボーディングを挟まずそのまま元の行き先へ戻す。
            next_target = _safe_next(request)

            if next_target:
                return redirect(next_target)

            return redirect("accounts:onboarding_tenant")

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


def _selectable_tenants(user):
    """選べるテナントを、選ぶ前に読める情報付きで返す。

    「切り替えると何が見えるようになるのか」を選択と同じ画面に出すため、
    件数は 1 クエリで一緒に取る（選択肢ごとに数えると N+1 になる）。
    """

    if user.is_superuser:
        tenants = Tenant.objects.filter(is_active=True)
    else:
        tenants = Tenant.objects.filter(pk=user.tenant_id, is_active=True)

    return tenants.annotate(user_count=Count("users", distinct=True)).order_by("name")


def _initial_tenant_id(request: HttpRequest, tenants: list) -> str:
    """最初に概要を開いておくテナント。いま参照中のものを優先する。"""

    current = _current_tenant_id(request)
    ids = [str(tenant.pk) for tenant in tenants]

    if current in ids:
        return current

    return ids[0] if ids else ""


def _current_tenant_id(request: HttpRequest) -> str:
    """いま参照しているテナント。未選択なら空文字。"""

    tenant = getattr(request, "tenant", None)

    return str(tenant.pk) if tenant else ""


@login_required
def select_tenant(request: HttpRequest) -> HttpResponse:
    """テナント切替。自分の所属テナント以外は選べない。

    ヘッダーのテナント名から来る画面。押した人はログイン直後と同じ選択画面を
    期待するため、オンボーディングと同じ2ペイン（左=一覧 / 右=概要）を出す。
    """

    tenants = _selectable_tenants(request.user)

    if request.method == "POST":
        tenant_id = request.POST.get("tenant")

        if tenants.filter(pk=tenant_id).exists():
            request.session[TENANT_SESSION_KEY] = str(tenant_id)

            return redirect(settings.LOGIN_REDIRECT_URL)

    choices = list(tenants)

    return render(
        request,
        "pages/select_tenant.html",
        {
            "tenants": choices,
            "selected_tenant_id": _initial_tenant_id(request, choices),
            "current_tenant_id": _current_tenant_id(request),
            "destination_label": "計数ダッシュボード",
            # 切替後は必ずダッシュボードへ行く。`next` を受け取っても使わないので、
            # 指定があることと使われないことを画面で明示する。
            "next_target": _safe_next(request),
            "page_title": "テナント選択",
        },
    )


@login_required
def onboarding_tenant(request: HttpRequest) -> HttpResponse:
    """ログイン直後、参照するテナントを選ぶ（複数テナントを横断できる利用者だけ）。

    通常の利用者は所属テナントが1つに定まるため選ぶまでもない。
    その場合は自動でスキップし、ダッシュボードへ進む。
    """

    tenants = _selectable_tenants(request.user)

    if tenants.count() <= 1:
        return redirect(settings.LOGIN_REDIRECT_URL)

    if request.method == "POST":
        tenant_id = request.POST.get("tenant")

        if tenants.filter(pk=tenant_id).exists():
            request.session[TENANT_SESSION_KEY] = str(tenant_id)

            return redirect(settings.LOGIN_REDIRECT_URL)

    choices = list(tenants)

    return render(
        request,
        "pages/onboarding_tenant.html",
        {
            "tenants": choices,
            "selected_tenant_id": _initial_tenant_id(request, choices),
            "current_tenant_id": _current_tenant_id(request),
            "destination_label": "計数ダッシュボードへ進みます",
            "page_title": "テナント選択",
        },
    )
