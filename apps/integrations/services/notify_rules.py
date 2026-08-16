"""「何を通知するか」のしきい値。

通知は送りすぎると読まれなくなる。読まれない通知は、無いのと同じどころか
「見ているはず」という誤解を生むぶん害がある。だから既定は**狭く**取る。

しきい値をここへ集めているのは、運用しながら調整する値だから。
判定ロジックの中に数字を散らすと、どこを触れば静かになるのかが分からなくなる。
"""

from __future__ import annotations

from apps.dashboard.models import Alert

#: 通知するアラートの重要度。注意（warning）・情報（info）は送らない。
#: 予兆検知は 1 案件で日に何件も出るので、重大だけに絞らないと即座に無視される。
NOTIFY_ALERT_SEVERITIES: tuple[str, ...] = (Alert.Severity.CRITICAL,)

#: 重大アラートの一次対応期限（検知からの日数）。
#: Alert 自体は期限を持たないため、SLA としてここで定義する。
#: 「いつまでに動くのか」が書かれていない通知は、読んでも行動に変わらない。
ALERT_RESPONSE_SLA_DAYS: int = 1

#: 介入提案を通知する最低信頼度。これ未満は人を呼ぶ前に精度を上げるべき段階。
#: なお信頼度 null（ルールベース提案）は AI の当て推量ではないので対象に含める。
MIN_PROPOSAL_CONFIDENCE: float = 0.6

#: 判断されずに滞留している提案・変更要求を通知するまでの日数。
STALE_APPROVAL_DAYS: int = 3

#: 期限超過タスクを通知するまでの超過日数。
#: 1 日の遅れは日常的に起きて自力で戻るので、通知するとノイズにしかならない。
MIN_OVERDUE_DAYS: int = 3

#: 一覧形式の通知に載せる最大件数。全件載せると読まれない。
MAX_DIGEST_ITEMS: int = 20


class Trigger:
    """通知の契機。`NotificationLog.trigger` の先頭に入る。

    抑止キーは `<契機>:<対象ID>` の形にする。NotificationLog に専用の
    「対象ID」列が無いため、trigger を対象単位の一意キーとして使う。
    契機ごとに接頭辞を分けているので、同じ提案でも「作成時」と「滞留時」は
    別の通知として 1 回ずつ送られる（片方が他方を抑止しない）。
    """

    ALERT = "alert"
    PROPOSAL = "proposal"
    STALE = "stale"
    OVERDUE = "overdue"


def dedupe_key(trigger: str, object_id: object) -> str:
    """抑止キーを作る。CharField(64) に収まるよう UUID 前提の長さで組む。"""

    return f"{trigger}:{object_id}"[:64]
