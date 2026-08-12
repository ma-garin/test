"""URL ルーティング。

画面ごとのパスは各アプリの urls.py に置き、ここでは束ねるだけにする。
ナビゲーションの URL 名は `apps.core.navigation` が参照する。
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core.views import healthz

handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"

urlpatterns = [
    path("", include("apps.dashboard.urls")),
    path("healthz/", healthz, name="healthz"),
    path("accounts/", include("apps.accounts.urls")),
    path("core/", include("apps.core.urls")),
    path("projects/", include("apps.projects.urls")),
    path("forecast/", include("apps.forecast.urls")),
    path("graph/", include("apps.graph.urls")),
    path("documents/", include("apps.documents.urls")),
    path("rag/", include("apps.rag.urls")),
    path("agents/", include("apps.agents.urls")),
    path("pmo/", include("apps.pmo.urls")),
    path("audit/", include("apps.audit.urls")),
    path("integrations/", include("apps.integrations.urls")),
    path("django-admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # デザイン案（docs/idea/）を開発サーバーから直接開けるようにする。
    # 静的HTMLなので本番では配信しない。
    urlpatterns += static("/idea/", document_root=settings.BASE_DIR / "docs" / "idea")
