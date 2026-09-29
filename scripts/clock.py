"""作業時間の補助計測。**報告の根拠にはしない**（AI が書き換えられるため）。実測の根拠は GitHub の PR merged_at（CLAUDE.md）。
verify は報告文の「実測:」行が規定の形かを検査する（Stop hook から呼ばれる）。

  python scripts/clock.py start "<作業名>" [--est 25]   # 着手を記録（JST）
  python scripts/clock.py end [--cp "12→14"]            # 完了。「見積 / 実測 / CP往復」の行を出す
  python scripts/clock.py show                            # 記録一覧

記録は ~/.qa-sentinel/clock.json（リポジトリには入れない）。時刻は機械の時計（JST 表示）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
STORE = Path.home() / ".qa-sentinel" / "clock.json"


def _load() -> dict:
    return json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else {"open": None, "done": []}


def _save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _fmt(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(JST).strftime("%H:%M")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("name"); s.add_argument("--est", type=int, help="見積（分）")
    e = sub.add_parser("end"); e.add_argument("--cp", default="", help="CP往復 見積→実績（例 12→14）")
    sub.add_parser("show")
    v = sub.add_parser("verify", help="報告文（stdin）の『実測:』行が計測記録と一致するか。不一致なら exit 2"); v.add_argument("--text", help="stdin の代わり")
    a = ap.parse_args(argv)
    d = _load()
    now = datetime.now(timezone.utc)
    if a.cmd == "start":
        d["open"] = {"name": a.name, "est": a.est, "start": now.isoformat()}
        _save(d)
        print(f"着手 {now.astimezone(JST).strftime('%Y-%m-%d %H:%M')} JST: {a.name}" + (f"（見積 {a.est} 分）" if a.est else ""))
    elif a.cmd == "end":
        o = d.get("open")
        if not o:
            print("未計測（start が無い）"); return 1
        mins = max(1, round((now - datetime.fromisoformat(o["start"])).total_seconds() / 60))
        est = o.get("est")
        line = f"見積: {est}分 / " if est else "見積: 未記入 / "
        line += f"実測: {mins}分（{_fmt(o['start'])}→{now.astimezone(JST).strftime('%H:%M')} JST、機械計測）"
        if a.cp:
            line += f" / CP往復: {a.cp}"
        if est and not (0.67 <= mins / est <= 1.5):
            line += f" ※比 {mins / est:.2f}: 原因を 1 行添える"
        d["done"].append({**o, "end": now.isoformat(), "mins": mins, "line": line}); d["open"] = None
        _save(d)
        print(line)
    elif a.cmd == "verify":
        text = a.text if a.text is not None else sys.stdin.read()
        import re
        ok_form = re.compile(r"実測:\s*\d+分（PR #\d+ マージ \d{2}:\d{2} → PR #\d+ マージ \d{2}:\d{2} JST、GitHub 時刻）")
        bad = [ln.strip() for ln in text.splitlines() if "実測:" in ln and "未計測" not in ln and not ok_form.search(ln)]
        if bad:
            print("実測の行が規定の形（GitHub 時刻・PR 番号つき）ではない:\n  " + "\n  ".join(bad)
                  + "\n→ 『実測: N分（PR #a マージ HH:MM → PR #b マージ HH:MM JST、GitHub 時刻）』か『実測: 未計測』にする")
            return 2
        print("実測の行は規定の形（または未記載）")
    else:
        for r in d["done"][-20:]:
            print(f"{_fmt(r['start'])}→{_fmt(r['end'])} JST  {r['mins']:>3} 分  見積 {r.get('est') or '-'}  {r['name']}")
        if d.get("open"):
            print(f"計測中: {d['open']['name']}（{_fmt(d['open']['start'])} JST〜）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
