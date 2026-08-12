from django.apps import AppConfig

from apps.graph.ontology import RelationType, register_endpoints

#: Signal と予測が入ってから使えるようになる関係。
#: グラフのオントロジーは 1 か所に置き、追加はアプリ起動時に宣言する。
SIGNAL_ENDPOINTS = {
    RelationType.DISCUSSED_IN: (
        ("graph.feature", "forecast.signal"),
        ("projects.defect", "forecast.signal"),
        ("projects.issue", "forecast.signal"),
        ("projects.wbstask", "forecast.signal"),
    ),
    RelationType.EVIDENCED_BY: (
        ("graph.feature", "forecast.signal"),
        ("projects.defect", "forecast.signal"),
        ("projects.issue", "forecast.signal"),
        ("projects.wbstask", "forecast.signal"),
    ),
    RelationType.CAUSED_BY: (("projects.defect", "forecast.signal"),),
    RelationType.FORECASTS: (
        ("forecast.forecastsnapshot", "projects.milestone"),
        ("forecast.forecastsnapshot", "projects.wbstask"),
        ("forecast.forecastsnapshot", "graph.feature"),
    ),
}


class ForecastConfig(AppConfig):
    name = "apps.forecast"
    verbose_name = "ライブ着地予測"

    def ready(self) -> None:
        for relation_type, pairs in SIGNAL_ENDPOINTS.items():
            register_endpoints(relation_type, pairs)
