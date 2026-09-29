"""run_gate の 6 ケース（偽 scripts と偽 CSV を tmp_path に置く）。"""
import pytest

from qa_sentinel.core.gates import run_gate
from qa_sentinel.core.project import Project

HEADER = "テストID,ロール,対象機能,ツアー観点,テスト目的,前提条件,手順,期待される結果,severity,結果,実施日,実施者,DEF,根拠の版,仕様の状態"


def _project(tmp_path):
    cfg = {"project": {"lifecycle_dir": "docs/lifecycle", "cases_csv": "docs/quality/system_test_cases.csv", "branch": "main"}}
    return Project(name="p", root=tmp_path, config=cfg, env_md="")


def _script(tmp_path, name, body):
    d = tmp_path / "scripts"
    d.mkdir(exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")


def _row(tid, expected, state=""):
    return f"{tid},r,f,t,p,pre,step,{expected},S2,,,,,v1,{state}"


def test_unknown_gate(tmp_path):
    with pytest.raises(ValueError):
        run_gate("nope", [], _project(tmp_path), cwd=tmp_path)


def test_missing_script(tmp_path):
    r = run_gate("trace-check", [], _project(tmp_path), cwd=tmp_path)
    assert r.exit_code == 127 and r.ok is False and r.summary.startswith("missing:")


def test_ok_with_report_path(tmp_path):
    _script(tmp_path, "trace_check.py", 'print("NG=0"); print("詳細: out/report.md")\n')
    r = run_gate("trace-check", [], _project(tmp_path), cwd=tmp_path)
    assert r.ok is True and r.report_path == "out/report.md"


def test_cases_csv(tmp_path):
    p = _project(tmp_path)
    csv_path = tmp_path / p.config["project"]["cases_csv"]
    csv_path.parent.mkdir(parents=True)
    rows = [HEADER, _row("ST-013", "ok", "失効(ST-013)"), _row("ST-014", ""), _row("ST-015", "ok", "確認待ち: 仕様変更")]
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    r = run_gate("cases", [], p, cwd=tmp_path)
    assert r.ok is False and "ST-014" in r.summary and "ST-015" in r.summary and "ST-013" not in r.summary


def test_impact_requires_id(tmp_path):
    with pytest.raises(ValueError):
        run_gate("impact", [], _project(tmp_path), cwd=tmp_path)


def test_exit_2_is_skipped(tmp_path):
    _script(tmp_path, "trace_check.py", "import sys; sys.exit(2)\n")
    r = run_gate("trace-check", [], _project(tmp_path), cwd=tmp_path)
    assert r.ok is False and r.skipped is True
