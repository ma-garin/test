from django.urls import path

from apps.documents import views

app_name = "documents"

urlpatterns = [
    path("", views.document_list, name="list"),
    path("upload/", views.upload, name="upload"),
    path("templates/", views.template_list, name="template_list"),
]
