from django.urls import path

from apps.forecast import views

app_name = "forecast"

urlpatterns = [
    path("", views.live_forecast, name="live"),
    path("features/<uuid:pk>/", views.feature_detail, name="feature_detail"),
    path("report/", views.daily_report, name="report"),
    path("snapshots/<uuid:pk>/review/", views.review_snapshot, name="review_snapshot"),
    path("links/<uuid:pk>/review/", views.review_work_link, name="review_work_link"),
]
