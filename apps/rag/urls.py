from django.urls import path

from apps.rag import views

app_name = "rag"

urlpatterns = [
    path("search/", views.search_view, name="search"),
    path("chat/", views.chat_view, name="chat"),
]
