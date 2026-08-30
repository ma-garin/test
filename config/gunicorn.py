"""本番の WSGI サーバ設定。

    gunicorn config.wsgi:application -c config/gunicorn.py

ワーカー数は `2 × CPU + 1` を既定にする。この画面群は待ち時間の大半が
DB ではなく Python 側の集計（組織 186・要員 650 で SQL 95ms に対し
Python 400ms）なので、CPU の数がそのまま同時にさばける数になる。

実測（組織186・要員650・実績9,600行、CPU 8 / ワーカー9）:

    同時  1 人  中央値   51ms  p95    51ms
    同時  4 人  中央値  110ms  p95   547ms
    同時  8 人  中央値  117ms  p95   682ms
    同時 16 人  中央値  173ms  p95 1,160ms

ワーカーを 4 に絞ると同時16人の中央値が 773ms まで落ちる。
台数を削るときは、ここが最初に悪くなる。
"""

import multiprocessing
import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

#: CPU 数から決める。環境変数で上書きできる（コンテナで CPU 制限がある場合など）。
workers = _int_env("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1)

#: 集計中に他のリクエストを取りこぼさないための余裕。増やしすぎても
#: GIL があるため CPU 律速の処理は速くならない。
threads = _int_env("GUNICORN_THREADS", 2)

#: 月次取込は数千行を1リクエストで処理する。既定の 30 秒では足りない。
timeout = _int_env("GUNICORN_TIMEOUT", 120)

#: 長時間動かしたワーカーを入れ替え、メモリの持ち越しを断つ。
max_requests = _int_env("GUNICORN_MAX_REQUESTS", 1000)
max_requests_jitter = 100

accesslog = "-"
errorlog = "-"
