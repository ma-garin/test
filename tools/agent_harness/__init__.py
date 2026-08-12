"""開発エージェント・ハーネス（AH-01〜AH-04）。

このパッケージは Django アプリではなく、`docs/改善に.md` を実装するエージェントが
「次に何をするか」「何を検証するか」「失敗をどう分類して止まるか」を
会話履歴ではなくファイルから復元するための道具である。

- `queue`    : AH-01 機械可読な作業キューと再開手順
- `registry` : AH-02 チケット種別ごとの検証レジストリ
- `failures` : AH-03 失敗分類と 3 回で保留する再試行制御
"""

from tools.agent_harness.failures import FailureCategory, classify_failure
from tools.agent_harness.queue import Ticket, TicketQueue
from tools.agent_harness.registry import VERIFICATION_REGISTRY, checks_for

__all__ = [
    "FailureCategory",
    "Ticket",
    "TicketQueue",
    "VERIFICATION_REGISTRY",
    "checks_for",
    "classify_failure",
]
