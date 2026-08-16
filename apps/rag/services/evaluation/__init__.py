"""RAG 評価基盤（traceability #68〜#71）。

品質を測れないまま「根拠追跡可能」を名乗らないための土台。
外部 API を前提にせず、既定の `local_hash` Embedding だけで全経路が通る。

- `metrics`      : 指標の純関数（Recall@K / Precision@K / MRR）
- `golden`       : Golden Dataset の参照と欠損検知
- `retrieval`    : 検索評価（#68 / #69）
- `answer`       : 回答評価 dry-run（#70）
- `static_check` : 索引と Golden の静的整合（#71）
- `runner`       : 実行と履歴保存、前回との差分
"""

from apps.rag.services.evaluation.runner import (
    METRIC_DEFINITIONS,
    MetricDelta,
    metric_deltas,
    previous_run,
    run_evaluation,
)

__all__ = [
    "METRIC_DEFINITIONS",
    "MetricDelta",
    "metric_deltas",
    "previous_run",
    "run_evaluation",
]
