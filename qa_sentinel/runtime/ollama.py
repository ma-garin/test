"""Ollama ランタイム（ローカル LLM。鍵不要）。呼び出し形は base.Runtime の 3 メソッド。

- `/api/chat` にツール呼び出し（run_gate・read_file・list_files・write_file）を渡し、ホスト側で実行する
- 段の結果は Managed Agents と同じ JSON 契約（agent/register.py の RESULT_CONTRACT）。根拠が無ければ blocked
- サンドボックスは無い。書き込みは project.config["project"]["subdir"] の下、かつ docs/ か tests/ か .qa-sentinel/ だけ。
  製品コードは書かず .qa-sentinel/outputs/<task>/fix_proposal.md に案を書く
- 予算は金額でなくトークン: config [session].max_tokens（既定 200000）。超えたら stopped "budget_reached"
- 接続先は環境変数 OLLAMA_HOST（既定 http://127.0.0.1:11434）、モデルは OLLAMA_MODEL か config [session].ollama_model（既定 qwen2.5:14b）
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ..core.event import ChangeEvent
from ..core.ledger import Task
from ..core.project import Project
from .base import PhaseResult, SessionHandle
from .managed_agents import ManagedAgentsRuntime, phase_prompt

DEFAULT_MODEL = "qwen2.5:14b"
MAX_ROUNDS = 24
WRITABLE = ("docs/", "tests/", ".qa-sentinel/")

TOOLS: list[dict] = [
    {"type": "function", "function": {"name": "run_gate", "description": "終了条件スクリプトを実行し exit code と要約を返す（合否はホストが判定）",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "list_files", "description": "対象フォルダのファイル一覧（相対パス）",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "テキストファイルを読む（先頭 20000 字）",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "docs/・tests/・.qa-sentinel/ の下だけ書ける。製品コードは書かず fix_proposal.md に案を書く",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
]


def _post_json(url: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - ローカル Ollama
        return json.loads(res.read().decode("utf-8"))


class OllamaRuntime:
    name = "ollama"

    def __init__(self, model: str | None = None, host: str | None = None, post: Callable[[str, dict], dict] | None = None):
        self.model = model
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        self._post = post or _post_json
        self._n = 0
        self._tokens = 0
        self._max_tokens = 200000

    # ---- start -------------------------------------------------------------
    def start(self, task: Task, event: ChangeEvent, project: Project) -> SessionHandle:
        sess = project.config.get("session", {})
        self.model = self.model or os.environ.get("OLLAMA_MODEL") or sess.get("ollama_model") or DEFAULT_MODEL
        self._max_tokens = int(sess.get("max_tokens", self._max_tokens))
        self._n += 1
        self._tokens = 0
        return SessionHandle(id=f"ollama_{task.task}_{self._n}", budget_usd=project.budget_usd)

    # ---- tools ---------------------------------------------------------------
    def _root(self, project: Project) -> Path:
        return Path(project.config.get("project", {}).get("subdir") or ".").resolve()

    def _safe(self, project: Project, rel: str) -> Path | None:
        root = self._root(project)
        p = (root / rel).resolve()
        return p if str(p).startswith(str(root)) else None

    def _tool(self, name: str, args: dict, task: Task, project: Project) -> tuple[str, tuple[str, bool] | None]:
        """ツールを実行して (結果文字列, 直近 gate) を返す。例外は文字列で返しセッションを壊さない。"""
        try:
            if name == "run_gate":
                from ..core.gates import run_gate
                g = run_gate(str(args.get("name", "")), list(args.get("args") or []), project, cwd=self._root(project))
                return json.dumps({"gate": g.name, "exit_code": g.exit_code, "ok": g.ok, "skipped": g.skipped, "summary": g.summary}, ensure_ascii=False), (g.name, g.ok)
            if name == "list_files":
                p = self._safe(project, str(args.get("path", ".")))
                if p is None or not p.exists():
                    return "error: path outside project or missing", None
                items = sorted(str(x.relative_to(self._root(project))) for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts)
                return "\n".join(items[:400]), None
            if name == "read_file":
                p = self._safe(project, str(args.get("path", "")))
                if p is None or not p.is_file():
                    return "error: not a file inside project", None
                return p.read_text(encoding="utf-8", errors="replace")[:20000], None
            if name == "write_file":
                rel = str(args.get("path", "")).replace("\\", "/")
                p = self._safe(project, rel)
                if p is None or not rel.startswith(WRITABLE):
                    return f"error: write not allowed ({rel}). 書けるのは {', '.join(WRITABLE)} の下だけ。製品コードは .qa-sentinel/outputs/{task.task}/fix_proposal.md に案を書く", None
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(str(args.get("content", "")), encoding="utf-8")
                return f"written: {rel}", None
            return f"error: unknown tool {name}", None
        except Exception as e:  # noqa: BLE001
            return f"error: {type(e).__name__}: {e}", None

    # ---- run_phase -----------------------------------------------------------
    def run_phase(self, handle: SessionHandle, phase: str, task: Task, event: ChangeEvent, project: Project) -> PhaseResult:
        from ..agent.register import system_prompt
        system = (system_prompt() + "\n\n## この環境\n"
                  f"対象フォルダ: {self._root(project)}（ツールのパスはここからの相対）。サンドボックスは無い。\n"
                  f"書けるのは {', '.join(WRITABLE)} の下だけ。段の結果 JSON は最後の返答の ```json ブロックに出す（ファイルには書かなくてよい）。\n\n"
                  "## 環境情報（env.md）\n" + project.env_md)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": phase_prompt(phase, task, event, repo_mounted=True)}]
        last_gate: tuple[str, bool] | None = None
        texts: list[str] = []
        try:
            for _ in range(MAX_ROUNDS):
                res = self._post(f"{self.host}/api/chat", {"model": self.model, "messages": messages, "tools": TOOLS, "stream": False})
                self._tokens += int(res.get("prompt_eval_count", 0) or 0) + int(res.get("eval_count", 0) or 0)
                handle.spent_usd = 0.0  # ローカルは無料。上限はトークンで見る
                if self._tokens > self._max_tokens:
                    return PhaseResult("stopped", "budget_reached", evidence=[f"tokens {self._tokens} > {self._max_tokens}"])
                msg = res.get("message") or {}
                messages.append(msg)
                calls = msg.get("tool_calls") or []
                if not calls:
                    if msg.get("content"):
                        texts.append(str(msg["content"]))
                    break
                for c in calls:
                    fn = (c.get("function") or {})
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError:
                            args = {}
                    out, gate = self._tool(str(fn.get("name", "")), args, task, project)
                    if gate is not None:
                        last_gate = gate
                    messages.append({"role": "tool", "content": out})
            else:
                return PhaseResult("stopped", "not_converged", evidence=[f"tool rounds > {MAX_ROUNDS}"])
        except urllib.error.URLError as e:
            return PhaseResult("stopped", f"runtime_error: ollama unreachable ({self.host}): {e.reason}")
        except Exception as e:  # noqa: BLE001
            return PhaseResult("stopped", f"runtime_error: {type(e).__name__}")
        data = ManagedAgentsRuntime._json_from_text(texts)
        r = ManagedAgentsRuntime._to_result(data, last_gate, 0.0)
        r.evidence.append(f"ollama: {self.model} tokens {self._tokens}")
        return r

    # ---- end -----------------------------------------------------------------
    def end(self, handle: SessionHandle) -> None:
        handle.ended = True
