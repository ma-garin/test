"""ログインとテナント切替。"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.forms import EmailLoginForm
from apps.accounts.models import Tenant
from apps.core.middleware import PROJECT_SESSION_KEY, TENANT_SESSION_KEY


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

        if _selectable(tenants, tenant_id):
            request.session[TENANT_SESSION_KEY] = str(tenant_id)
            # テナントを変えたら案件の選択は無効。他テナントの案件で
            # 絞り込んだまま画面を見せると、空の一覧の理由が分からなくなる。
            request.session.pop(PROJECT_SESSION_KEY, None)
            messages.success(request, "参照するテナントを切り替えました。")

            return redirect(_back_to(request))

        # 押したのに何も起きない画面にしない。理由は伝えるが、
        # 存在の有無（他テナントが実在するか）は伝えない。
        messages.error(request, "指定されたテナントは選択できません。")

    return render(
        request,
        "pages/select_tenant.html",
        {"tenants": tenants, "page_title": "テナント選択"},
    )


@login_required
def select_project(request: HttpRequest) -> HttpResponse:
    """案件切替。

    PMO は複数案件を担当するため、対象を1件へ絞れないと数字が混ざる。
    選択は任意で、未選択なら参照できる全案件を横断して見る。

    旧実装の `project_store.py` に相当する。Django 版で欠落していた
    （`docs/INCIDENT-001-scope-omission.md` 参照）。
    """

    from apps.projects.selectors import projects_for

    projects = projects_for(request.user, request.tenant)

    if request.method == "POST":
        raw = request.POST.get("project", "")

        if not raw:
            # 「全案件」を選び直したとき。絞り込みを外す。
            request.session.pop(PROJECT_SESSION_KEY, None)
            messages.success(request, "全案件を対象にしました。")

            return redirect(_back_to(request))

        if _selectable(projects, raw):
            request.session[PROJECT_SESSION_KEY] = str(raw)
            messages.success(request, "対象案件を切り替えました。")

            return redirect(_back_to(request))

        # 参照できない案件を指定された。存在の有無は伝えない。
        messages.error(request, "指定された案件は選択できません。")

    return render(
        request,
        "pages/select_project.html",
        {
            "projects": projects.order_by("code"),
            "current": getattr(request, "project", None),
            "next": _back_to(request),
            "page_title": "案件の切替",
        },
    )


def _selectable(queryset, raw) -> bool:
    """`raw` が選択可能な対象か。

    主キーは UUID なので、フォームを手で書き換えて `abc` を送ると
    `filter(pk=...)` が `ValidationError` を投げ、500 になる。
    不正値は「選べない」であって「エラー」ではない。
    """

    if not raw:
        return False

    try:
        return queryset.filter(pk=raw).exists()
    except (ValidationError, ValueError, TypeError):
        return False


def _back_to(request: HttpRequest) -> str:
    """切替後の戻り先。自ホスト宛てのときだけ採用する。"""

    target = request.POST.get("next") or request.GET.get("next") or ""

    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target

    return "/"
