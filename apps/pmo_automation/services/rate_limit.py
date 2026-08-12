"""安全施策.md SC-08 / SEC-11: 大量流入イベントに対する上限機構（部分実装）。

tenant単位で、直近の一定期間に作成されたWork Item数が上限を超えたら
`RateLimitExceeded`を送出する。呼び出し側（将来のintakeコマンド等）は
これを捕まえて「捨てずに隔離（rate_limited扱い）」できる。

このモジュールが提供するのは「上限機構」だけである。安全施策.md SC-08
が同時に求める「循環検出」「最大依存深度」「dead-letter queue」は、
現在のP0データモデル（WorkStep.order は単一Plan内の逐次実行のみで、
Work Item間の依存グラフ自体が存在しない）には該当する対象が無いため
実装していない。具体的な上限値（max_count / window_seconds）は
このプロジェクトの既存慣習（config/settings/base.py の DETECTION_RULES
と同じ「判定ロジックへ数値を埋め込まない」方針）に倣い、呼び出し側が
上書きできる引数として持つ。実運用の値は人が決める事項として残る
（安全施策.md 11章6: tenant・project・connector・operationのrate limit）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

#: 既定値。実運用のチューニングは人が決める事項（安全施策.md 11章6）。
DEFAULT_MAX_INTAKE_PER_WINDOW = 100
DEFAULT_WINDOW_SECONDS = 60


class RateLimitExceeded(Exception):
    """指定期間内のWork Item作成数が上限に達したことを表す。"""

    def __init__(self, *, tenant_code: str, count: int, max_count: int, window_seconds: int) -> None:
        self.tenant_code = tenant_code
        self.count = count
        self.max_count = max_count
        self.window_seconds = window_seconds
        super().__init__(
            f"直近{window_seconds}秒でtenant={tenant_code}のWork Item作成が"
            f"{count}件に達しました（上限{max_count}件）。"
        )


def check_intake_rate_limit(
    tenant,
    *,
    now: datetime,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    max_count: int = DEFAULT_MAX_INTAKE_PER_WINDOW,
) -> None:
    """直近window_seconds以内のtenantのWork Item作成数が上限以上ならRateLimitExceededを送出する。

    DB書き込みは一切行わない（判定だけを行う）。

    呼び出し規約（セキュリティレビュー指摘: 呼び出し元が無ければ機能しない）:
    このチェックは各intake関数（intake.py/intake_forecast.py/
    intake_quality.py/intake_rag.py）の新規Work Item作成直前に組み込み済み。
    新しくバッチ処理の呼び出し元を追加する場合は、必ず
    `RateLimitExceeded`をキャッチして該当イベントをスキップし、
    バッチ全体を失敗させないこと（run_pmo_automation.pyを参照）。
    キャッチせず例外を伝播させたままにすると、1件の上限到達で
    バッチ全体が失敗する。
    """

    from apps.pmo_automation.models import PmoWorkItem

    since = now - timedelta(seconds=window_seconds)
    count = PmoWorkItem.objects.filter(tenant=tenant, created_at__gte=since).count()
    if count >= max_count:
        raise RateLimitExceeded(
            tenant_code=tenant.code, count=count, max_count=max_count, window_seconds=window_seconds
        )
