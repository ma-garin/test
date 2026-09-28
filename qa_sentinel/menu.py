"""番号で選ぶメニュー。`qa-sentinel` を引数なしで打つとこれが出る（黒い画面が苦手な人向け）。

コマンド名を覚えなくてよい。裏では cli.py の run / ok / reject / results / answer / approve を呼ぶ。
"""
from __future__ import annotations

import sys
import webbrowser

from .core.ledger import Ledger
from .core.plan import PRESETS
from .core.state import PHASE_LABELS

_WHO = {"review": "AI の下書きを見て OK か差し戻し", "handoff": "自分でやって結果を渡す", "blocked": "AI の質問に答える",
        "paused": "最終承認をする", "running": "AI が作業中", "done": "完了", "stopped": "止まっています（理由を見る）", "queued": "待機"}
_PRESET_HELP = {"M1": "全部 AI（最後だけ確認）", "M2": "準備まで AI、テスト実行は自分（いつもの使い方）", "M3": "段ごとに毎回確認",
                "M4": "設計まで AI", "M5": "実行と分析だけ AI", "M6": "古いテストの入れ替えだけ AI", "M7": "自分で作って AI に見てもらう", "M8": "ペア"}


def ask(prompt: str, default: str = "") -> str:
    v = input(f"{prompt}{f' [{default}]' if default else ''} › ").strip()
    return v or default


def main(tasks_dir: str = "tasks", projects_dir: str = "projects", port: int = 8790) -> int:
    from .cli import main as cli
    base = ["--tasks", tasks_dir, "--projects", projects_dir]
    ledger = Ledger(tasks_dir)
    while True:
        print("\n=== qa-sentinel ===")
        todo = [t for t in ledger.list() if t.status in ("review", "handoff", "blocked", "paused", "stopped")]
        print(f"あなたの番: {len(todo)} 件")
        print("  1) 変更を伝えて始める")
        print("  2) やることリスト（自分の番のもの）")
        print("  3) ブラウザで開く（同じ内容を画面で）")
        print("  4) 全部の一覧")
        print("  0) 終わる")
        c = ask("番号")
        if c == "0":
            return 0
        if c == "1":
            project = ask("プロジェクト名", "library-loan")
            nl = ask("何が変わりましたか（1 文）")
            if not nl:
                print("変更の説明が空です"); continue
            print("どこまで AI にやらせますか:")
            for k, v in _PRESET_HELP.items():
                print(f"  {k}) {v}")
            mode = ask("番号（M1〜M8）", "M2").upper()
            if mode not in PRESETS:
                print("M1〜M8 で選んでください"); continue
            who = ask("だれが確認しますか（あなたの名前）")
            budget = ask("いくらまで使ってよいですか（USD）", "5")
            cli(base + ["run", "--project", project, "--nl", nl, "--mode", mode, "--reviewer", who, "--budget", budget])
        elif c in ("2", "4"):
            items = todo if c == "2" else ledger.list()
            if not items:
                print("なし"); continue
            for i, t in enumerate(items, 1):
                print(f"  {i}) {t.task}  {PHASE_LABELS.get(t.phase, t.phase)}  → {_WHO.get(t.status, t.status)}  {t.reason or ''}")
            pick = ask("開く番号（Enter で戻る）")
            if not pick.isdigit() or not (1 <= int(pick) <= len(items)):
                continue
            t = items[int(pick) - 1]
            cli(base + ["show", t.task])
            who = t.plan.get("reviewer", "") if t.plan else ""
            if t.status == "review":
                a = ask("a) OK して次へ  b) 差し戻す  Enter) 戻る")
                if a == "a":
                    cli(base + ["ok", t.task, "--by", ask("あなたの名前", who), "-m", ask("直した点（任意）")])
                elif a == "b":
                    cli(base + ["reject", t.task, "--by", ask("あなたの名前", who), "-m", ask("理由")])
            elif t.status == "handoff":
                if ask("結果を渡しますか a) はい  Enter) 戻る") == "a":
                    cli(base + ["results", t.task, "--by", ask("あなたの名前", who), "--file", ask("成果物のパス（任意）"), "-m", ask("見たまま（任意）")])
            elif t.status == "blocked":
                ans = ask("AI の質問への答え")
                if ans:
                    cli(base + ["answer", t.task, "--by", ask("あなたの名前", who), ans])
            elif t.status == "paused":
                if ask("最終承認しますか a) はい  Enter) 戻る") == "a":
                    cli(base + ["approve", t.task, "--by", ask("あなたの名前", who)])
        elif c == "3":
            url = f"http://127.0.0.1:{port}/"
            print(f"ブラウザで {url} を開きます（この画面で web を起動していない場合は、別の黒い画面で `qa-sentinel web` を先に）")
            webbrowser.open(url)
        else:
            print("0〜4 で選んでください")


if __name__ == "__main__":
    sys.exit(main())
