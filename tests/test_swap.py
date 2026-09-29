"""swap（regression-swap）のテスト。gate は fake に差し替える。"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path
from types import SimpleNamespace

import qa_sentinel.core.swap as sw
from qa_sentinel.core.project import Project

FIX = Path(__file__).parent / "fixtures" / "swap"
NEW = [{"対象機能": "返却", "テスト目的": "返却正常系", "手順": "返却する", "prefix": "ST", "req_id": "REQ-F-003"}]


def _setup(tmp_path, csv_name="system_test_cases.csv"):
    (tmp_path / "docs/quality").mkdir(parents=True)
    (tmp_path / "docs/lifecycle").mkdir(parents=True)
    shutil.copy(FIX / csv_name, tmp_path / "docs/quality/system_test_cases.csv")
    shutil.copy(FIX / "traceability-matrix.md", tmp_path / "docs/lifecycle/traceability-matrix.md")
    cfg = {"project": {"lifecycle_dir": "docs/lifecycle", "cases_csv": "docs/quality/system_test_cases.csv", "branch": "main"}}
    return Project(name="p", root=tmp_path, config=cfg, env_md="")


def _gate(monkeypatch, ok=True):
    res = SimpleNamespace(ok=ok, exit_code=0 if ok else 1, skipped=False, summary="ok" if ok else "trace NG")
    monkeypatch.setattr(sw, "run_gate", lambda *a, **k: res)


def _rows(tmp_path):
    with open(tmp_path / "docs/quality/system_test_cases.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_retire_and_add(tmp_path, monkeypatch):
    p = _setup(tmp_path)
    _gate(monkeypatch)
    r = sw.swap(p, ["REQ-F-003"], [dict(c) for c in NEW], cwd=tmp_path)
    assert r.ok is True and r.retired == ["ST-001", "ST-003"] and r.added == ["ST-004"]
    rows = {x["テストID"]: x for x in _rows(tmp_path)}
    assert rows["ST-001"]["仕様の状態"] == "失効(ST-004)" and rows["ST-003"]["仕様の状態"] == "失効(ST-004)"
    assert rows["ST-002"]["仕様の状態"] == ""
    assert rows["ST-004"]["対象機能"] == "返却" and rows["ST-004"]["根拠の版"] == "REQ-F-003@"


def test_matrix_updated(tmp_path, monkeypatch):
    p = _setup(tmp_path)
    _gate(monkeypatch)
    sw.swap(p, ["REQ-F-003"], [dict(c) for c in NEW], cwd=tmp_path)
    text = (tmp_path / "docs/lifecycle/traceability-matrix.md").read_text(encoding="utf-8")
    assert "| ST-001, ST-003, ST-004 |" in text
    assert "| ST-002 |" in text


def test_gate_ng_restores(tmp_path, monkeypatch):
    p = _setup(tmp_path)
    _gate(monkeypatch, ok=False)
    c, m = tmp_path / "docs/quality/system_test_cases.csv", tmp_path / "docs/lifecycle/traceability-matrix.md"
    before = (c.read_bytes(), m.read_bytes())
    r = sw.swap(p, ["REQ-F-003"], [dict(c_) for c_ in NEW], cwd=tmp_path)
    assert r.ok is False and r.reason == "trace NG" and r.retired == [] and r.added == []
    assert (c.read_bytes(), m.read_bytes()) == before


def test_legacy_csv(tmp_path, monkeypatch):
    p = _setup(tmp_path, "legacy_cases.csv")
    _gate(monkeypatch)
    r = sw.swap(p, ["REQ-F-003"], [dict(c) for c in NEW], cwd=tmp_path)
    assert r.ok is True and r.added == ["ST-003"] and r.retired == []
    with open(tmp_path / "docs/quality/system_test_cases.csv", encoding="utf-8", newline="") as f:
        assert next(csv.reader(f)) == sw.CSV_HEADER
    rows = {x["テストID"]: x for x in _rows(tmp_path)}
    assert rows["ST-001"]["テスト目的"] == "貸出正常系" and rows["ST-002"]["手順"] == "ログインする"
