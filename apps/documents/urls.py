from django.urls import path

from apps.documents import views

app_name = "documents"

urlpatterns = [
    path("", views.document_list, name="list"),
    path("upload/", views.upload, name="upload"),
    path("<uuid:pk>/extract/", views.extract_document, name="extract"),
    path("templates/", views.template_list, name="template_list"),
    path("templates/<uuid:pk>/export/", views.template_export, name="template_export"),
]
