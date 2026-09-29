"""Managed Agents ランタイム。呼び出し形は docs/01_設計仕様.md §6、API 形状は Anthropic の Managed Agents（beta managed-agents-2026-04-01）。

司令塔は base.Runtime の 3 メソッドしか呼ばない。ここで守ること:
- sessions.create に budget（金額上限。amount はセント単位の整数文字列）を必ず渡す
- agent.custom_tool_use(run_gate) はホスト側でスクリプトを実行し user.custom_tool_result を返す。LLM に exit code を解釈させない
- session.status_idle の stop_reason が budget_reached なら PhaseResult("stopped", "budget_reached")
- 人待ち（blocked / paused）では end() でセッションを終了する。待たない
- API キーは環境変数 ANTHROPIC_API_KEY からだけ読む。ファイルから読まない・ログに出さない
- 必須の環境変数は ANTHROPIC_API_KEY だけ。GITHUB_TOKEN が無ければリポジトリを積まずに動く（根拠にその旨を残す）
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from typing import Any, Iterable

from ..core.event import ChangeEvent
from ..core.ledger import Task
from ..core.project import Project
from .base import PhaseResult, SessionHandle

try:  # anthropic は任意依存（pip install -e .[managed]）。無くても import できるようにする
    from anthropic import Anthropic
except ImportError:  # pragma: no cover - 環境依存
    Anthropic = None  # type: ignore[assignment]

BETA = "managed-agents-2026-04-01"
OUTPUTS_DIR = "/mnt/session/outputs"
RESULT_NAME = "phase_result.json"
_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """SDK のオブジェクトでも dict でも同じように属性を取る。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def usd_to_cents(usd: float) -> str:
    """budget.max_list_cost.amount はセント単位の整数文字列（$5.00 → "500"）。"""
    return str(int(round(float(usd) * 100)))


def cents_to_usd(amount: Any) -> float:
    """session.usage の list_cost.amount（セント文字列）を USD に。数値ならそのまま USD とみなす。"""
    if amount is None:
        return 0.0
    if isinstance(amount, str):
        return int(amount) / 100 if amount.lstrip("-").isdigit() else float(amount)
    return float(amount)


def stop_reason_of(ev: Any) -> str:
    """stop_reason はオブジェクト {type, event_ids} か文字列。type を返す。"""
    sr = _get(ev, "stop_reason")
    if sr is None:
        return ""
    return str(_get(sr, "type", sr) or "")


class _Client:
    """SDK への薄いアダプタ。触る口はここだけ（テストの偽クライアントが差し替わる）。"""

    def __init__(self, sdk: Any):
        self.sdk = sdk

    @property
    def _beta(self) -> Any:
        return self.sdk.beta

    def upload_file(self, fh: Any) -> Any:
        return self._beta.files.upload(file=fh)

    def create_session(self, **kwargs: Any) -> Any:
        return self._beta.sessions.create(**kwargs)

    def send(self, session_id: str, events: list[dict]) -> Any:
        return self._beta.sessions.events.send(session_id=session_id, events=events)

    def stream(self, session_id: str) -> Any:
        return self._beta.sessions.events.stream(session_id=session_id)

    def list_output_files(self, session_id: str) -> list[Any]:
        page = self._beta.files.list(scope_id=session_id, betas=[BETA])
        data = _get(page, "data", page)
        return list(data or [])

    def download(self, file_id: str) -> str:
        raw = self._beta.files.download(file_id)
        for attr in ("text", "content"):
            v = _get(raw, attr)
            if callable(v):
                v = v()
            if isinstance(v, (str, bytes)):
                raw = v
                break
        if hasattr(raw, "read"):
            raw = raw.read()
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    def archive(self, session_id: str) -> Any:
        return self._beta.sessions.archive(session_id)


def phase_prompt(phase: str, task: Task, event: ChangeEvent, repo_mounted: bool) -> str:
    where = "/workspace/repo" if repo_mounted else "（リポジトリ未マウント。/workspace/env.md の情報だけで進め、成果物は outputs に書く）"
    return (
        f"段 {phase}。\n"
        f"ChangeEvent: {event.to_json()}\n"
        f"resume_from: {event.resume_from}\n"
        f"対象リポジトリ: {where}\n\n"
        f"終了時は {OUTPUTS_DIR}/{RESULT_NAME} に "
        '{"impact":[...],"cases_added":[...],"cases_retired":[...],"evidence":[...]} を書くこと'
        '（任意で "artifacts":{...}, "draft":{...}|null, "question":null|"..." も入れてよい）。'
        "同じ JSON を最後の返答の ```json ブロックにも出すこと。\n"
        f"書き込みは {task.branch or '（作業ブランチ未設定）'} のみ。"
        f"製品コードは直さず {OUTPUTS_DIR}/fix_proposal.md に原因と直し方の案を書くこと。\n"
        "終了条件は run_gate ツールで判定する（exit code を自分で解釈しない）。"
        "期待結果を 1 つに決められないなど人の判断が要るときは question に質問を書いて止まること。"
    )


class ManagedAgentsRuntime:
    name = "managed"

    def __init__(self, project_name: str | None = None, client: Any = None):
        # ここでは何も読まない（agent.toml・環境変数は start() で遅延解決）
        self.project_name = project_name
        self.agent_id = self.agent_version = self.environment_id = None
        self.repo_mounted = False
        self._client: _Client | None = _Client(client) if client is not None else None

    # ---- start -------------------------------------------------------------
    def start(self, task: Task, event: ChangeEvent, project: Project) -> SessionHandle:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("環境変数が無い: ['ANTHROPIC_API_KEY']（鍵を入れると動く。docs/07 §5）")

        path = project.root / "agent.toml"
        if not path.exists():
            raise RuntimeError(f"agent.toml が無い: {path}（先に python -m qa_sentinel.agent.register --project {project.name} を実行する）")
        cfg = tomllib.loads(path.read_text(encoding="utf-8"))
        agent = cfg.get("agent", {})
        self.agent_id, self.agent_version = agent.get("id"), agent.get("version")
        if not self.agent_id or not self.agent_version:
            raise RuntimeError(f"agent.toml に [agent] id/version が無い: {path}（register.py で登録し直す）")
        self.environment_id = os.environ.get("QA_SENTINEL_ENV_ID") or cfg.get("environment", {}).get("id")
        if not self.environment_id:
            raise RuntimeError("環境 ID が無い（register.py が agent.toml に [environment] id を書く。または QA_SENTINEL_ENV_ID）")

        if self._client is None:
            if Anthropic is None:
                raise RuntimeError("anthropic が入っていない（pip install -e .[managed]）")
            self._client = _Client(Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]))
        client = self._client

        with (project.root / "env.md").open("rb") as fh:
            env_file = client.upload_file(fh)
        resources: list[dict] = [{"type": "file", "file_id": _get(env_file, "id"), "mount_path": "/workspace/env.md"}]
        proj = project.config.get("project", {})
        token = os.environ.get("GITHUB_TOKEN")
        self.repo_mounted = bool(token and proj.get("repo"))
        if self.repo_mounted:
            repo = {"type": "github_repository", "url": "https://github.com/" + proj["repo"],
                    "authorization_token": token, "mount_path": "/workspace/repo"}
            if proj.get("branch"):
                repo["checkout"] = {"type": "branch", "name": proj["branch"]}
            resources.insert(0, repo)
        session = client.create_session(
            agent={"type": "agent", "id": self.agent_id, "version": self.agent_version},
            environment_id=self.environment_id,
            title=f"{task.task} {event.source}",
            budget={"type": "limit", "max_list_cost": {"amount": usd_to_cents(project.budget_usd), "currency": "USD"}},
            resources=resources,
        )
        return SessionHandle(id=_get(session, "id"), budget_usd=project.budget_usd)

    # ---- run_phase ---------------------------------------------------------
    def _run_gate(self, ev: Any, project: Project) -> tuple[str, int, bool, str]:
        """(gate 名, exit_code, ok, summary)。例外は外へ出さない。"""
        inp = _get(ev, "input", {}) or {}
        name = str(_get(inp, "name", ""))
        try:
            from ..core.gates import run_gate  # 遅延 import（T01）
            gate = run_gate(name, list(_get(inp, "args", []) or []), project,
                            cwd=project.config.get("project", {}).get("subdir", "."))
            return name, int(gate.exit_code), bool(gate.ok), str(gate.summary)
        except ValueError as e:
            return name, 2, False, str(e)
        except Exception as e:  # noqa: BLE001 - セッションを壊さない。未検査として返す
            return name, 2, False, f"{type(e).__name__}: {e}"

    def _fetch_result(self, session_id: str) -> dict:
        try:
            for f in self._client.list_output_files(session_id):
                name = str(_get(f, "filename", "") or _get(f, "path", "") or _get(f, "name", ""))
                if name == RESULT_NAME or name.endswith("/" + RESULT_NAME):
                    data = json.loads(self._client.download(_get(f, "id")))
                    return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 - 取れなければ根拠なしとして扱う
            return {}
        return {}

    @staticmethod
    def _json_from_text(texts: list[str]) -> dict:
        for text in reversed(texts):
            for m in reversed(_JSON_BLOCK.findall(text)):
                try:
                    data = json.loads(m)
                except ValueError:
                    continue
                if isinstance(data, dict):
                    return data
        return {}

    @staticmethod
    def _texts(ev: Any) -> list[str]:
        out = []
        for block in _get(ev, "content", []) or []:
            t = _get(block, "text")
            if isinstance(t, str):
                out.append(t)
        return out

    @staticmethod
    def _iter(stream: Any) -> Iterable[Any]:
        if hasattr(stream, "__enter__"):
            with stream as s:
                yield from s
        else:
            yield from stream

    def run_phase(self, handle: SessionHandle, phase: str, task: Task, event: ChangeEvent, project: Project) -> PhaseResult:
        if self._client is None:
            return PhaseResult("stopped", "not_started")
        client = self._client
        try:
            stream = client.stream(handle.id)  # stream-first: 開いてから送る（取りこぼし防止）
            client.send(handle.id, [{"type": "user.message",
                                     "content": [{"type": "text", "text": phase_prompt(phase, task, event, self.repo_mounted)}]}])
            last_gate: tuple[str, bool] | None = None
            texts: list[str] = []
            for ev in self._iter(stream):
                etype = _get(ev, "type")
                if etype == "agent.custom_tool_use":
                    if _get(ev, "name") != "run_gate":
                        continue
                    name, exit_code, ok, summary = self._run_gate(ev, project)
                    last_gate = (name, ok)
                    client.send(handle.id, [{"type": "user.custom_tool_result", "custom_tool_use_id": _get(ev, "id"),
                                             "content": [{"type": "text", "text": json.dumps({"gate": name, "exit_code": exit_code, "ok": ok, "summary": summary}, ensure_ascii=False)}],
                                             "is_error": False}])
                elif etype == "agent.message":
                    texts.extend(self._texts(ev))
                elif etype == "session.usage":
                    handle.spent_usd = cents_to_usd(_get(_get(ev, "list_cost", {}) or {}, "amount"))
                elif etype == "session.status_idle":
                    reason = stop_reason_of(ev)
                    if reason == "requires_action":
                        continue  # ツール結果待ち。上で返している
                    if reason == "budget_reached":
                        return PhaseResult("stopped", "budget_reached", spent_usd=handle.spent_usd)
                    data = self._fetch_result(handle.id) or self._json_from_text(texts)
                    r = self._to_result(data, last_gate, handle.spent_usd)
                    if not self.repo_mounted:
                        r.evidence.append("repo: 未マウント（GITHUB_TOKEN なし）")
                    return r
                elif etype == "session.status_terminated":
                    return PhaseResult("stopped", "terminated", spent_usd=handle.spent_usd)
        except Exception as e:  # noqa: BLE001 - 司令塔へ例外を出さず stopped で人へ渡す
            return PhaseResult("stopped", f"runtime_error: {type(e).__name__}", spent_usd=handle.spent_usd)
        return PhaseResult("stopped", "stream_ended", spent_usd=handle.spent_usd)

    @staticmethod
    def _to_result(data: dict, last_gate: tuple[str, bool] | None, spent: float) -> PhaseResult:
        def _list(key: str) -> list[str]:
            v = data.get(key) or []
            return [str(x) for x in v] if isinstance(v, list) else []

        artifacts = data.get("artifacts") if isinstance(data.get("artifacts"), dict) else {}
        draft = data.get("draft") if isinstance(data.get("draft"), dict) else None
        r = PhaseResult("ok", impact=_list("impact"), cases_added=_list("cases_added"),
                        cases_retired=_list("cases_retired"), evidence=_list("evidence"),
                        artifacts=artifacts, draft=draft, spent_usd=spent)
        question = data.get("question")
        if isinstance(question, str) and question.strip():
            r.outcome, r.reason = "blocked", f"確認待ち: {question.strip()}"
        elif not r.evidence:
            r.outcome, r.reason = "blocked", "no_evidence"  # 根拠の無い下書きは確定に出さない
        elif last_gate is not None and last_gate[1] is False:
            r.outcome, r.reason = "blocked", f"gate_ng:{last_gate[0]}"
        return r

    # ---- end ---------------------------------------------------------------
    def end(self, handle: SessionHandle) -> None:
        try:
            if self._client is not None:
                self._client.archive(handle.id)
        except Exception:  # noqa: BLE001 - 終了処理で落とさない
            pass
        handle.ended = True
