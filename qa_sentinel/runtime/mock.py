"""モックランタイム。LLM を使わず、全段を決定論的に進める。MVP・テスト・Web の動作確認用。

blocked_at / stopped_at を指定すると、その段で止まる経路を再現する。
"""
from __future__ import annotations

from ..core.event import ChangeEvent
from ..core.ledger import Task
from ..core.project import Project
from ..core.state import PHASES
from .base import PhaseResult, SessionHandle

_STEP_COST = 0.4  # 1 段あたりの見せかけの消費（USD）


class MockRuntime:
    name = "mock"

    def __init__(self, blocked_at: str | None = None, stopped_at: str | None = None):
        self.blocked_at, self.stopped_at = blocked_at, stopped_at
        self._n = 0

    def start(self, task: Task, event: ChangeEvent, project: Project) -> SessionHandle:
        self._n += 1
        return SessionHandle(id=f"mock_{task.task}_{self._n}", budget_usd=project.budget_usd)

    def run_phase(self, handle: SessionHandle, phase: str, task: Task, event: ChangeEvent, project: Project) -> PhaseResult:
        handle.spent_usd += _STEP_COST
        if phase == self.stopped_at or handle.spent_usd > handle.budget_usd:
            return PhaseResult("stopped", reason="budget_reached" if handle.spent_usd > handle.budget_usd else "not_converged", spent_usd=handle.spent_usd)
        if phase == self.blocked_at:
            return PhaseResult("blocked", reason="確認待ち: 期待結果を 1 つに決められない", spent_usd=handle.spent_usd)
        r = PhaseResult("ok", spent_usd=handle.spent_usd, evidence=[f"mock: {phase} gate exit 0"])
        ids = event.changed_ids or ["REQ-F-003"]
        if phase == "test-plan":
            r.impact = [f"BD-{i:03d}" for i in range(2, 2 + len(ids))] + ["DD-004", "UT-011", "ST-005"]
            r.artifacts["plan"] = "docs/quality/iso29119-test-plan.md"
            r.evidence.append("trace-check --impact REQ-F-003: exit 0")
            r.draft = {"title": "テスト計画（影響分析）", "path": r.artifacts["plan"],
                       "text": f"変更: {event.text or '（本文なし）'}\n影響 ID: {', '.join(r.impact)}\n範囲: 貸出（上限判定）・返却（上限解除）・画面の残数表示\n"
                               "対象外: 認証・検索（変更の影響なし）\n入替対象: ST-005（上限 5 冊の境界）→ 失効し、3 冊版を追加"}
        elif phase == "test-design":
            r.cases_added = ["ST-013", "ST-014", "UT-020"]
            r.evidence.append("spec.md#4 貸出上限")
            r.draft = {"title": "ケース設計（追加 3 件・失効 1 件）", "path": project.config.get("project", {}).get("cases_csv", "docs/test/system_test_cases.csv"), "cases": [
                {"id": "ST-005", "target": "貸出", "purpose": "上限 5 冊の境界", "steps": "4 冊借りた状態で 5 冊目を借りる", "expected": "借りられる", "sev": "High", "basis": "REQ-F-003@v2", "state": "retired"},
                {"id": "ST-013", "target": "貸出", "purpose": "上限 3 冊の境界（内側）", "steps": "2 冊借りた状態で 3 冊目を借りる", "expected": "借りられ、残数 0 と表示", "sev": "High", "basis": "REQ-F-003@v3", "state": "new"},
                {"id": "ST-014", "target": "貸出", "purpose": "上限 3 冊の境界（外側）", "steps": "3 冊借りた状態で 4 冊目を借りる", "expected": "「上限 3 冊」のエラーで拒否", "sev": "Critical", "basis": "REQ-F-003@v3", "state": "new"},
                {"id": "UT-020", "target": "LoanService.canBorrow", "purpose": "上限判定の単体", "steps": "count=2,3,4 で呼ぶ", "expected": "true,false,false", "sev": "High", "basis": "REQ-F-003@v3", "state": "new"}]}
        elif phase == "test-implementation":
            r.draft = {"title": "実行手順（次の段はあなたがやります）", "steps": ["venv\\Scripts\\python -m pytest tests/ -k loan", "ブラウザで library-loan.html を開き、ST-013・ST-014 を手順どおりに実施", "結果を results/<task>.csv に PASS/FAIL で記録し、「渡す」で提出"]}
        elif phase == "regression-swap":
            r.draft = {"title": "入替の内容", "text": "失効: ST-005（仕様の状態 = 失効(ST-013)。行は消さない）\n追加: ST-013, ST-014, UT-020\n追跡表: REQ-F-003 → ST-013, ST-014, UT-020 に更新\ngate: trace-check NG=0 / test-weaken-check 未検査（git なし）"}
        elif phase == "test-completion":
            r.artifacts["report"] = "docs/quality/iso29119-test-completion-report.md"
        elif phase == "regression-swap":
            r.cases_retired = ["ST-005"]
        return r

    def end(self, handle: SessionHandle) -> None:
        handle.ended = True


assert set(PHASES)  # 参照を残す（順序の定義は state.py）
