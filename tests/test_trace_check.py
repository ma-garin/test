"""trace_check.py のテスト（5 ケース）。"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "demo" / "library-loan" / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "trace"
DEMO = ROOT / "demo" / "library-loan" / "docs" / "lifecycle"
sys.path.insert(0, str(SCRIPTS))
import section_hash  # noqa: E402
import trace_check  # noqa: E402


def run(capsys, d: Path, tmp_path: Path, *extra: str):
    rc = trace_check.main([str(d), "-o", str(tmp_path / "report.md"), *extra])
    return rc, capsys.readouterr().out


def fx(tmp_path: Path) -> Path:
    d = tmp_path / "lc"
    shutil.copytree(FIXTURE, d)
    return d


def test_demo_and_fixture_ok(capsys, tmp_path):
    rc, out = run(capsys, DEMO, tmp_path)
    assert rc == 0 and out.splitlines()[0].startswith("✅ NG=0（定義済み ID: ")
    rc, out = run(capsys, FIXTURE, tmp_path)
    assert rc == 0 and out.splitlines()[0].startswith("✅ NG=0")


def test_c1_duplicate_definition(capsys, tmp_path):
    d = fx(tmp_path)
    (d / "03-detailed-design.md").write_text("### BD-001 重複\n", encoding="utf-8")
    rc, out = run(capsys, d, tmp_path)
    assert rc == 1 and "重複定義" in out


def test_c2_undefined_reference(capsys, tmp_path):
    d = fx(tmp_path)
    with (d / "02-basic-design.md").open("a", encoding="utf-8") as fh:
        fh.write("\nDD-999 を参照する。\n")
    rc, out = run(capsys, d, tmp_path)
    assert rc == 1 and "未定義参照" in out


def test_impact(capsys, tmp_path):
    rc, out = run(capsys, DEMO, tmp_path, "--impact", "REQ-F-001")
    assert rc == 0 and "影響範囲: REQ-F-001" in out
    rc, out = run(capsys, FIXTURE, tmp_path, "--impact", "REQ-F-001")
    assert rc == 0 and "BD-001" in out and "UAT-001" in out


def test_c7_suspect(capsys, tmp_path):
    d = fx(tmp_path)
    m = d / "traceability-matrix.md"
    m.write_text(m.read_text(encoding="utf-8").replace("BD-001 ", "BD-001@0000000 "), encoding="utf-8")
    assert section_hash.current_hashes(section_hash.lifecycle_files(d))["BD-001"] != "0000000"
    rc, out = run(capsys, d, tmp_path)
    assert rc == 1 and "C7 suspect" in out
