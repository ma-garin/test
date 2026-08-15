"""共通画面と、認証・権限・通信経路で失敗したときの共通画面。"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from apps.core.navigation import all_items


@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    """死活監視用。認証不要、DB へも触れない。"""

    return JsonResponse({"status": "ok"})


def _log_operation(
    request: HttpRequest, action: str, target: str, *, ok: bool, detail: str = ""
) -> None:
    """設定変更を監査ログへ残す。

    誰がいつ AI 設定を変えたかは、回答の質が変わった原因を後から追うための唯一の
    手掛かりになる。値そのものは残さない（`OperationLog.save()` がマスクするが、
    そもそも渡さない）。
    """

    tenant = getattr(request, "tenant", None)

    if tenant is None:
        return

    from apps.audit.models import OperationLog

    OperationLog.objects.create(
        tenant=tenant,
        user=request.user if request.user.is_authenticated else None,
        action=action,
        target=target,
        succeeded=ok,
        detail=detail,
    )


@login_required
def settings_page(request: HttpRequest) -> HttpResponse:
    """AI プロバイダ等の設定画面。

    3段構成（個人設定 / テナント既定 / 環境変数）。ロールに関係なく全利用者が
    自分ぶんの API 設定を持てる。API キーは費用と利用ログの単位が個人なので、
    管理者の1本を全員で共有すると、誰の利用で費用が出たのか追えなくなる。

    テナント既定を編集できるのはテナント管理者だけ。ここは画面内で分ける。
    ナビ定義（`apps.core.navigation`）も同じく全ロールへ出す宣言に揃えてある。

    値の表示は必ずマスク済みのものを使う（`apps.core.services.ai_settings`）。
    生の API キーをテンプレートへ渡してはいけない。
    """

    from apps.core.forms import TenantAISettingForm, UserAISettingForm
    from apps.core.models import TenantAISetting, UserAISetting
    from apps.core.services.ai_settings import (
        SCOPE_LABELS,
        effective_config,
        masked_ai_settings,
        personal_credentials_allowed,
        verify_connection,
    )

    tenant = getattr(request, "tenant", None)
    can_manage_tenant = request.user.is_tenant_admin
    personal_allowed = personal_credentials_allowed(tenant)

    user_setting = UserAISetting.objects.filter(user=request.user).first() or UserAISetting(
        user=request.user
    )
    tenant_setting = None

    if tenant is not None:
        tenant_setting = TenantAISetting.objects.filter(
            tenant=tenant
        ).first() or TenantAISetting(tenant=tenant)

    # 個人設定を空欄にしたときに何が使われるか。画面へ出すために先に解いておく。
    inherited = effective_config(user=None, tenant=tenant)

    user_form = UserAISettingForm(instance=user_setting, inherited=inherited)
    tenant_form = (
        TenantAISettingForm(instance=tenant_setting) if tenant_setting is not None else None
    )
    connection = None

    if request.method == "POST":
        scope = request.POST.get("scope", "")

        if scope == "user":
            if not personal_allowed:
                messages.error(
                    request,
                    "このテナントでは利用者ごとの API 設定が許可されていません。"
                    "テナント管理者へ相談してください。",
                )

                return redirect("core:settings")

            user_form = UserAISettingForm(
                request.POST, instance=user_setting, inherited=inherited
            )

            if user_form.is_valid():
                saved = user_form.save(commit=False)
                saved.user = request.user
                saved.save()
                _log_operation(
                    request,
                    "AI設定の更新（個人）",
                    str(request.user),
                    ok=True,
                    detail=f"プロバイダ={saved.provider or '上位に従う'}",
                )
                messages.success(request, "個人の AI 設定を保存しました。")

                return redirect("core:settings")

            messages.error(request, "入力内容を確認してください。")

        elif scope == "tenant":
            if not can_manage_tenant:
                _log_operation(
                    request,
                    "AI設定の更新（テナント）",
                    str(tenant),
                    ok=False,
                    detail="権限なし",
                )

                raise PermissionDenied("テナント既定の変更はテナント管理者のみ行えます")

            if tenant_setting is None:
                # 権限はあるがテナントが定まっていない（未所属・未選択）。
                # そのまま保存すると tenant が NULL になり保存時に落ちる。
                messages.error(
                    request,
                    "対象のテナントが決まっていません。先にテナントを選んでください。",
                )

                return redirect("core:settings")

            tenant_form = TenantAISettingForm(request.POST, instance=tenant_setting)

            if tenant_form.is_valid():
                saved = tenant_form.save(commit=False)
                saved.tenant = tenant
                saved.save()
                _log_operation(
                    request,
                    "AI設定の更新（テナント）",
                    str(tenant),
                    ok=True,
                    detail=(
                        f"プロバイダ={saved.provider or '環境変数に従う'} / "
                        f"個人設定={'許可' if saved.allow_personal_credentials else '禁止'}"
                    ),
                )
                messages.success(request, "テナント既定の AI 設定を保存しました。")

                return redirect("core:settings")

            messages.error(request, "入力内容を確認してください。")

        elif scope == "verify":
            # 保存済みの実効設定で疎通を確かめる。キーを保存できても有効とは限らず、
            # 無効なまま運用に入ると検索が黙って local_hash へ退避する。
            connection = verify_connection(effective_config(user=request.user, tenant=tenant))
            _log_operation(
                request,
                "AI接続確認",
                connection.provider,
                ok=connection.ok,
                detail=connection.message,
            )

        else:
            messages.error(request, "不明な操作です。")

    context = {
        "ai_settings": masked_ai_settings(user=request.user, tenant=tenant),
        "user_form": user_form,
        "tenant_form": tenant_form,
        "can_manage_tenant": can_manage_tenant,
        "personal_allowed": personal_allowed,
        "has_user_setting": user_setting.pk is not None,
        "connection": connection,
        "scope_labels": SCOPE_LABELS,
        "page_title": "AI設定",
    }

    return render(request, "pages/settings.html", context)


@login_required
def screen_map(request: HttpRequest) -> HttpResponse:
    """画面と移植状況の一覧。"""

    return render(
        request,
        "pages/screen_map.html",
        {"items": all_items(), "page_title": "画面マップ"},
    )


def bad_request(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "400.html", status=400)


def permission_denied(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "403.html", status=403)


def page_not_found(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "404.html", status=404)


def server_error(request: HttpRequest) -> HttpResponse:
    return render(request, "500.html", status=500)


@login_required
def not_implemented(request: HttpRequest) -> HttpResponse:
    """まだ実装していない画面の着地先。

    ナビゲーションで `status="planned"` の項目は、押しても何も起きないと
    「壊れている」と受け取られる。ここへ着地させ、戻り先と次の操作を出す。

    参照するビューが無いままテンプレートだけ置いておくと、レンダリングされない
    ＝壊れていても気づけない。URL を与えて、他画面と同じ検証の対象にする。
    """

    return render(
        request,
        "pages/not_implemented.html",
        {
            "page_title": "この画面はまだありません",
            "requested_label": request.GET.get("screen", "").strip()[:60],
            "return_to": request.GET.get("next") or "/",
        },
    )
