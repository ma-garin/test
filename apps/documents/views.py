"""ドキュメント管理画面。"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.documents.models import Document, Template


def _documents_for(request: HttpRequest):
    queryset = Document.objects.alive().select_related("project", "uploaded_by")

    if request.user.is_superuser and request.tenant is None:
        return queryset

    return queryset.filter(tenant=request.tenant or request.user.tenant)


@login_required
def document_list(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "pages/document_list.html",
        {"documents": _documents_for(request)[:200], "page_title": "ドキュメント登録"},
    )


@login_required
def template_list(request: HttpRequest) -> HttpResponse:
    templates = Template.objects.alive().filter(tenant=request.tenant or request.user.tenant)

    return render(
        request,
        "pages/template_list.html",
        {"templates": templates, "page_title": "ひな型管理"},
    )
