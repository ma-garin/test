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


def last_user_text(transcript: Path) -> str:
    text = ""
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "user":
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            if parts:
                text = "\n".join(parts)
    return text


WORK_WORDS = ("作って", "作成", "実装", "直して", "修正", "報告", "整理", "まとめ", "検証", "レビュー", "出して", "ください")
QUESTION_MAX = 400  # 質問への返答の上限（文字）。INC-0002


def question_violation(user: str, reply: str) -> str | None:
    """質問（？/?/〜か で終わる、作業依頼語なし）への返答が長い・結論が先頭に無いときに理由を返す。"""
    u = user.strip()
    if not u or "<" in u[:1]:
        return None
    is_q = u.endswith(("？", "?", "か", "か。", "のか")) and not any(w in u for w in WORK_WORDS)
    if not is_q:
        return None
    body = reply.strip()
    if len(body) > QUESTION_MAX:
        return f"質問への返答が {len(body)} 字（上限 {QUESTION_MAX}）。1 行目に答え、本文は 3 行まで（INC-0002）"
    if body.startswith(("|", "#", "-", "見積")):
        return "質問への返答の 1 行目が答えではない（表・見出し・箇条書き・見積で始まっている）（INC-0002）"
    return None


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
    qv = question_violation(last_user_text(Path(tp)), text)
    if qv:
        print(qv, file=sys.stderr)
        return 2
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
