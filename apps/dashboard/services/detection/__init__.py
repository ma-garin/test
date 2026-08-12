"""実データから危険を見つけるルールベース検知。

要件 #5（クリティカルパス影響）、#7（サイレント炎上）、#40（仕様変更頻度異常）、
#41（バグ率異常）、#66（介入提案の自動生成）を担う。

LLM は使わない（ADR-0003）。しきい値は `settings.DETECTION_RULES` に置き、
判定根拠は必ず `evidence` に残す。根拠を示せないアラートは無視されるようになり、
検知の仕組み全体が信用を失うため。
"""

from apps.dashboard.services.detection.findings import (
    Finding,
    Skip,
    SkipReason,
    kind_label,
)
from apps.dashboard.services.detection.runner import DetectionResult, run_detection

__all__ = [
    "DetectionResult",
    "Finding",
    "Skip",
    "SkipReason",
    "kind_label",
    "run_detection",
]
