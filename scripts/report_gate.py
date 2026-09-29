"""Stop hook: 最後の返答に書かれた「実測:」の行を clock.py の計測記録と機械照合する。
一致しなければ exit 2 で終了を拒否し、理由を Claude に返す（Claude の判断を経ない・保守者が settings.json に登録する）。

.claude/settings.json（保守者が当てる）:
{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 scripts/report_gate.py"}]}]}}
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def last_assistant_text(transcript: Path) -> str:
    text = ""
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "assistant":
            continue
        content = (rec.get("message") or {}).get("content") or []
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        if parts:
            text = "\n".join(parts)
    return text


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    if payload.get("stop_hook_active"):
        return 0  # 無限ループ防止（hook が返した後の再停止は通す）
    tp = payload.get("transcript_path")
    if not tp or not Path(tp).exists():
        return 0
    text = last_assistant_text(Path(tp))
    if "実測:" not in text:
        return 0
    r = subprocess.run([sys.executable, str(Path(__file__).with_name("clock.py")), "verify", "--text", text],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 2:
        print(r.stdout.strip(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
