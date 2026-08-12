"""機械可読な PMO 自律運用化パッケージの整合性を検証する。"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "docs" / "agent"
QUEUE_PATH = AGENT_DIR / "pmo_autopilot_queue.json"
CONTRACT_PATH = AGENT_DIR / "pmo_autopilot_contract.json"
DECISIONS_PATH = AGENT_DIR / "pmo_autopilot_decisions.json"
SCENARIOS_PATH = AGENT_DIR / "pmo_autopilot_scenarios.json"

VALID_TICKET_STATES = {"untouched", "in_progress", "done", "hold", "decision_required"}
VALID_PRIORITIES = {"P0", "P1", "P2"}
REQUIRED_TICKET_FIELDS = {
    "id",
    "priority",
    "kind",
    "depends_on",
    "state",
    "goal",
    "allowed_paths",
    "forbidden_actions",
    "acceptance",
    "required_scenarios",
    "required_reviews",
    "verification",
    "evidence",
}
REQUIRED_SCENARIO_FIELDS = {"id", "ticket_id", "title", "arrange", "act", "assertions", "safety_assertions"}


class ContractError(ValueError):
    """実装エージェントが推測で進めてはいけない矛盾を表す。"""


def _load(path: Path) -> dict:
    display_path = _display_path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ContractError(f"必要ファイルがありません: {display_path}") from error
    except json.JSONDecodeError as error:
        raise ContractError(f"JSON が不正です: {display_path}: {error.msg}") from error


def _display_path(path: Path) -> str:
    """リポジトリ外の一時 fixture でも検証エラーを安全に表示する。"""

    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_repository_contract(root: Path | None = None) -> dict[str, dict]:
    """契約四ファイルを読み、クロス参照を検証して返す。"""

    directory = (root or AGENT_DIR).resolve()
    package = {
        "contract": _load(directory / CONTRACT_PATH.name),
        "decisions": _load(directory / DECISIONS_PATH.name),
        "scenarios": _load(directory / SCENARIOS_PATH.name),
        "queue": _load(directory / QUEUE_PATH.name),
    }
    validate_package(package)
    return package


def validate_package(package: dict[str, dict]) -> None:
    """仕様の参照漏れ、循環、危険なP0設定を早期に拒否する。"""

    contract = package["contract"]
    queue = package["queue"]
    decisions = package["decisions"]
    scenarios = package["scenarios"]

    if contract.get("default_rollout_mode") != "shadow":
        raise ContractError("P0 の既定ロールアウトは shadow でなければなりません。")
    if set(contract.get("terminal_states", ())) - set(contract.get("states", ())):
        raise ContractError("terminal_states は states の部分集合でなければなりません。")
    _validate_transition_map(contract)

    tickets = queue.get("tickets", [])
    ticket_by_id = _index(tickets, "id", "ticket")
    scenario_by_id = _index(scenarios.get("scenarios", []), "id", "scenario")
    decision_by_id = _index(decisions.get("decisions", []), "id", "decision")

    for ticket in tickets:
        missing = REQUIRED_TICKET_FIELDS - set(ticket)
        if missing:
            raise ContractError(f"{ticket.get('id', '<unknown>')}: ticket 必須項目不足 {sorted(missing)}")
        if ticket["state"] not in VALID_TICKET_STATES:
            raise ContractError(f"{ticket['id']}: 未知の state {ticket['state']}")
        if ticket["priority"] not in VALID_PRIORITIES:
            raise ContractError(f"{ticket['id']}: 未知の priority {ticket['priority']}")
        unknown_dependencies = set(ticket["depends_on"]) - set(ticket_by_id)
        if unknown_dependencies:
            raise ContractError(f"{ticket['id']}: 未知の依存 {sorted(unknown_dependencies)}")
        unknown_scenarios = set(ticket["required_scenarios"]) - set(scenario_by_id)
        if unknown_scenarios:
            raise ContractError(f"{ticket['id']}: 未知の scenario {sorted(unknown_scenarios)}")
        if ticket["state"] == "done" and not ticket["evidence"]:
            raise ContractError(f"{ticket['id']}: evidence なしに done は禁止です。")
        if ticket["state"] in {"hold", "decision_required"} and not ticket.get("hold_reason"):
            raise ContractError(f"{ticket['id']}: {ticket['state']} には hold_reason が必要です。")
        if ticket["state"] == "done":
            _validate_ticket_review(ticket)
        _validate_ticket_decisions(ticket, decision_by_id)

    _validate_acyclic(ticket_by_id)
    _validate_scenarios(scenarios.get("scenarios", []), ticket_by_id)
    _validate_p0_safety(tickets, contract)


def _validate_transition_map(contract: dict) -> None:
    states = set(contract.get("states", ()))
    transitions = contract.get("allowed_transitions", {})
    if set(transitions) != states:
        raise ContractError("allowed_transitions は全 state をちょうど一回ずつ持たなければなりません。")
    for source, targets in transitions.items():
        unknown = set(targets) - states
        if unknown:
            raise ContractError(f"{source}: 未知の遷移先 {sorted(unknown)}")


def _validate_ticket_review(ticket: dict) -> None:
    """done チケットの review 記録が自己申告で水増しされていないかを静的に検証する。

    `cli.py` の `cmd_done` は CLI 経由の操作をこの条件でガードするが、
    queue.json を直接編集して done にした場合はそのガードを素通りできる。
    ここで validate_package 側にも同じ条件を持たせることで、
    ファイルの整合性そのものとして機械的に検知できるようにする
    （安全施策.md SC-02: reviewer を自由文字列で受けると自己レビューを
    偽装できるという懸念への、ハーネス内でできる範囲の対策）。

    ただし reviewer 文字列が実在の別主体であることまでは、このハーネス単体
    では証明できない（認証済み subject_id による検証は SA-03 のスコープ）。
    """

    review = ticket.get("review")
    if not review:
        raise ContractError(f"{ticket['id']}: done には review 記録が必要です。")
    # 空白文字列だけの値は「実質的に空」として扱う。キー削除や空文字列だけでなく
    # 空白のみの値でも reviewer==executor 判定を迂回できてしまうため
    # （レビュー指摘: 前回の "executor欠落" 修正の亜種）、strip 後の空も拒否する。
    executor = (ticket.get("executor") or "").strip()
    if not executor:
        raise ContractError(f"{ticket['id']}: done には executor の記録が必要です。")
    reviewer = (review.get("reviewer") or "").strip()
    if not reviewer:
        raise ContractError(f"{ticket['id']}: review に reviewer がありません。")
    if reviewer == executor:
        raise ContractError(f"{ticket['id']}: 実装者自身のレビューだけでは done にできません。")
    if review.get("failed"):
        raise ContractError(f"{ticket['id']}: review に failed 項目が残っています: {review['failed']}")
    if set(review.get("passed", ())) != set(ticket["required_reviews"]):
        raise ContractError(f"{ticket['id']}: required_reviews の承認が過不足なく揃っていません。")


def _validate_ticket_decisions(ticket: dict, decision_by_id: dict[str, dict]) -> None:
    for decision in decision_by_id.values():
        if ticket["id"] in decision.get("blocks", []) and decision.get("status") != "resolved":
            if ticket["state"] != "decision_required":
                raise ContractError(
                    f"{ticket['id']}: 未決 D-01〜D-05 により decision_required でなければなりません。"
                )


def _validate_scenarios(scenarios: list[dict], ticket_by_id: dict[str, dict]) -> None:
    for scenario in scenarios:
        missing = REQUIRED_SCENARIO_FIELDS - set(scenario)
        if missing:
            raise ContractError(f"{scenario.get('id', '<unknown>')}: scenario 必須項目不足 {sorted(missing)}")
        if scenario["ticket_id"] not in ticket_by_id:
            raise ContractError(f"{scenario['id']}: ticket_id が存在しません。")
        if not scenario["assertions"] or not scenario["safety_assertions"]:
            raise ContractError(f"{scenario['id']}: assertions と safety_assertions は必須です。")
        ticket = ticket_by_id[scenario["ticket_id"]]
        if scenario["id"] not in ticket["required_scenarios"]:
            raise ContractError(
                f"{scenario['id']}: 対応 ticket {ticket['id']} に required_scenarios がありません。"
            )


def _validate_p0_safety(tickets: list[dict], contract: dict) -> None:
    prohibited = "".join(contract.get("forbidden_in_p0", ()))
    for ticket in tickets:
        if ticket["priority"] != "P0":
            continue
        forbidden = "".join(ticket["forbidden_actions"])
        if "外部" not in forbidden and "external" not in forbidden:
            raise ContractError(f"{ticket['id']}: P0 ticket に外部副作用の禁止がありません。")
        if "dry_run" in ticket["goal"] and "dry-run" not in forbidden:
            raise ContractError(f"{ticket['id']}: dry-run ticket に dry-run 禁止条件がありません。")
    if "未承認の外部システム書き込み" not in prohibited:
        raise ContractError("P0 契約に未承認外部書き込み禁止がありません。")


def _index(items: list[dict], key: str, label: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in items:
        value = item.get(key)
        if not value:
            raise ContractError(f"{label} に {key} がありません。")
        if value in result:
            raise ContractError(f"{label} {value} が重複しています。")
        result[value] = item
    return result


def _validate_acyclic(tickets: dict[str, dict]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(ticket_id: str) -> None:
        if ticket_id in visited:
            return
        if ticket_id in visiting:
            raise ContractError(f"チケット依存に循環があります: {ticket_id}")
        visiting.add(ticket_id)
        for dependency in tickets[ticket_id]["depends_on"]:
            walk(dependency)
        visiting.remove(ticket_id)
        visited.add(ticket_id)

    for ticket_id in tickets:
        walk(ticket_id)
