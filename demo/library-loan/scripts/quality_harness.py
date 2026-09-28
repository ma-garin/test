#!/usr/bin/env python3
"""機能契約ハーネス（Functional Integrity Harness）— 汎用版。

quality/feature_contracts.yml（JSON 互換 YAML）を読み、
  1. 統制文書の存在  2. risk_level / status の妥当性  3. 参照パスの実在
  4. implemented に実行経路（route_files / core_files）があること
  5. critical / high に failure_modes と required_tests があること
  6. symbols が core_files に実在すること
  7. source_roots 配下の実装モジュールがどれかの契約か allowlist に登録されていること
  8. scan_roots に「UI only」「not implemented」等の未実装マーカーが無いこと
を検証する。NG>0 で exit 1（CI でそのまま落とせる）。

使い方: python3 scripts/quality_harness.py [--root DIR] [--contract PATH]
出所: WebSpec2Doc scripts/quality_harness.py（2026-07〜08、53 機能で運用）を設定駆動に汎用化。
規則の説明: skills/test-strategy/references/feature-contracts.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

RISK_LEVELS = {"critical", "high", "medium", "low"}
RISK_LEVELS_REQUIRING_FAILURE_TESTS = {"critical", "high"}
ALLOWED_STATUS = {"implemented", "partial", "planned"}
FORBIDDEN_STATUS = {"ui-only"}
DEFAULT_HARNESS = {
    "required_docs": ["quality/feature_contracts.yml"],
    "source_roots": [],
    "source_suffixes": [".py"],
    "unregistered_allowlist": {},
    "scan_roots": [],
    "suspicious_terms": ["UI only", "ui-only", "not implemented", "dummy endpoint", "stub endpoint"],
}


def _read_contracts(contract_file: Path) -> dict[str, Any]:
    if not contract_file.exists():
        raise AssertionError(f"missing contract file: {contract_file}")
    try:
        return json.loads(contract_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssertionError(f"feature contract must be JSON-compatible YAML: {exc}") from exc


def _harness_config(contracts: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(DEFAULT_HARNESS)
    cfg.update(contracts.get("harness", {}) or {})
    allow = cfg.get("unregistered_allowlist") or {}
    if isinstance(allow, list):  # 理由なしのリスト形式も受け付ける
        allow = {p: "" for p in allow}
    cfg["unregistered_allowlist"] = allow
    return cfg


def _file_text(root: Path, path_text: str) -> str:
    path = root / path_text
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _validate_feature(root: Path, feature: dict[str, Any], errors: list[str]) -> None:
    feature_id = feature.get("feature_id") or "<missing>"
    risk_level = feature.get("risk_level")
    status = feature.get("status")

    if not feature.get("feature_id"):
        errors.append("feature missing feature_id")
    if risk_level not in RISK_LEVELS:
        errors.append(f"{feature_id}: invalid risk_level={risk_level!r}")
    if status in FORBIDDEN_STATUS:
        errors.append(f"{feature_id}: ui-only features are forbidden")
    elif status not in ALLOWED_STATUS:
        errors.append(f"{feature_id}: invalid status={status!r}")

    for key in ("ui_files", "route_files", "core_files"):
        for path_text in feature.get(key, []) or []:
            if not (root / path_text).exists():
                errors.append(f"{feature_id}: missing referenced path: {path_text}")

    if status == "implemented" and not (feature.get("route_files") or feature.get("core_files")):
        errors.append(f"{feature_id}: implemented feature lacks route_files/core_files")

    if risk_level in RISK_LEVELS_REQUIRING_FAILURE_TESTS:
        if not feature.get("failure_modes"):
            errors.append(f"{feature_id}: {risk_level} feature lacks failure_modes")
        if not feature.get("required_tests"):
            errors.append(f"{feature_id}: {risk_level} feature lacks required_tests")

    core_files = feature.get("core_files", []) or []
    for symbol in feature.get("symbols", []) or []:
        if not any(symbol in _file_text(root, p) for p in core_files):
            errors.append(f"{feature_id}: symbol {symbol!r} not found in core_files")


def _validate_docs(root: Path, cfg: dict[str, Any], errors: list[str]) -> None:
    for path_text in cfg["required_docs"]:
        if not (root / path_text).exists():
            errors.append(f"missing required governance file: {path_text}")


def _validate_no_unimplemented_user_paths(root: Path, cfg: dict[str, Any], errors: list[str]) -> None:
    for root_text in cfg["scan_roots"]:
        scan_root = root / root_text
        if not scan_root.exists():
            continue
        for path in sorted(scan_root.rglob("*")):
            if not path.is_file() or path.suffix not in {".py", ".js", ".ts", ".html"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for term in cfg["suspicious_terms"]:
                if term in text:
                    errors.append(
                        f"suspicious incomplete user path: {path.relative_to(root)} contains {term!r}"
                    )


def _validate_all_modules_registered(
    root: Path, cfg: dict[str, Any], features: list[dict[str, Any]], errors: list[str]
) -> None:
    """source_roots 配下の実装モジュールが、必ずどれかの契約に紐づくことを確認する。

    これが無いと「機能を足したが契約に登録し忘れる」を人の注意力に頼ることになり、
    ハーネスが素通しで PASS を返す（2026-07-19 に実際に発生した）。
    """
    registered: set[str] = set()
    for feature in features:
        for key in ("core_files", "route_files", "ui_files"):
            registered.update(str(p) for p in (feature.get(key, []) or []))
    allow = cfg["unregistered_allowlist"]
    suffixes = set(cfg["source_suffixes"])

    for root_text in cfg["source_roots"]:
        src_root = root / root_text
        if not src_root.is_dir():
            continue
        for path in sorted(src_root.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            if path.name == "__init__.py" or "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            rel = str(path.relative_to(root))
            if rel in registered or rel in allow:
                continue
            errors.append(
                f"unregistered feature module: {rel} "
                "(feature_contracts.yml のいずれかの機能へ登録するか、"
                "基盤部品なら harness.unregistered_allowlist へ理由付きで追加すること)"
            )


def run(root: Path, contract_file: Path) -> int:
    errors: list[str] = []
    contracts = _read_contracts(contract_file)
    cfg = _harness_config(contracts)
    _validate_docs(root, cfg, errors)

    features_raw = contracts.get("features", [])
    features: list[dict[str, Any]] = []
    if not isinstance(features_raw, list) or not features_raw:
        errors.append("feature contracts must contain non-empty features list")
    else:
        for feature in features_raw:
            if not isinstance(feature, dict):
                errors.append("feature entry must be an object")
                continue
            features.append(feature)
            _validate_feature(root, feature, errors)

    _validate_all_modules_registered(root, cfg, features, errors)
    _validate_no_unimplemented_user_paths(root, cfg, errors)

    if errors:
        print("Functional Integrity Harness: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Functional Integrity Harness: PASS")
    print(f"validated_features={len(features)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Functional Integrity Harness")
    parser.add_argument("--root", default=".", help="プロジェクトルート（既定: カレント）")
    parser.add_argument("--contract", default="quality/feature_contracts.yml", help="契約ファイル（root 相対）")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    contract = Path(args.contract)
    if not contract.is_absolute():
        contract = root / contract
    try:
        return run(root, contract)
    except AssertionError as exc:
        print("Functional Integrity Harness: FAIL")
        print(f"- {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
