from django.urls import path

from apps.dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("", views.control, name="control"),
    path("tasks/", views.tasks, name="tasks"),
    path("progress/", views.progress, name="progress"),
    path("quality/", views.quality, name="quality"),
    path("risk/", views.risk, name="risk"),
    path("change/", views.change, name="change"),
    path("intervention/", views.intervention, name="intervention"),
    path(
        "intervention/<uuid:pk>/decide/",
        views.intervention_decide,
        name="intervention_decide",
    ),
    path("alerts/", views.alert_list, name="alert_list"),
    # 状態の書き換えなので POST のみ。GET で状態が変わると、リンクを踏んだだけで
    # アラートが消えたり、クローラの巡回で確定してしまう。
    path("alerts/<uuid:pk>/decide/", views.alert_decide, name="alert_decide"),
    path("detection/", views.detection, name="detection"),
    path("detection/run/", views.detection_run, name="detection_run"),
    path("ops-rules/", views.ops_rules, name="ops_rules"),
    path("kpi/", views.kpi, name="kpi"),
    path("poc/", views.poc, name="poc"),
]
