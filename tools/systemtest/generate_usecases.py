"""ユースケース一覧を CSV へ書き出す。

    python tools/systemtest/generate_usecases.py

`docs/systemtest/usecases/usecases.csv` を作る。手で編集しない。ケースを足すときは
`catalog.py` の軸を足す。手編集を許すと MECE が崩れ、重複と抜けが混ざる。
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from catalog import CSV_COLUMNS, PERSONAS, ROLES, STAGES, VIEWPOINTS, build_all  # noqa: E402

OUTPUT = Path(__file__).resolve().parents[2] / "docs" / "systemtest" / "usecases" / "usecases.csv"


def verify(cases) -> None:
    """MECE を機械で確かめる。目視では必ず見落とす。"""

    ids = [case.case_id for case in cases]
    duplicated = [key for key, count in Counter(ids).items() if count > 1]

    if duplicated:
        raise SystemExit(f"ケースIDが重複しています: {duplicated[:5]}")

    # 網羅（Exhaustive）: どの軸の値も必ず 15 回現れる。
    for role in ROLES:
        count = sum(1 for case in cases if case.axis == "role" and case.role == role)

        if count != len(VIEWPOINTS):
            raise SystemExit(f"ロール {role} のケースが {count} 件（期待 {len(VIEWPOINTS)} 件）")

    for persona in PERSONAS:
        count = sum(1 for case in cases if case.persona_id == persona.id)

        if count != len(STAGES):
            raise SystemExit(f"ペルソナ {persona.id} のケースが {count} 件（期待 {len(STAGES)} 件）")

    # 排他（Exclusive）: 同じ (軸, 対象, 観点) の組は 1 件だけ。
    keys = [(case.axis, case.role, case.persona_id, case.viewpoint_id) for case in cases]
    overlapping = [key for key, count in Counter(keys).items() if count > 1]

    if overlapping:
        raise SystemExit(f"観点が重複しています: {overlapping[:5]}")

    total = len(cases)

    if not 500 <= total <= 1000:
        raise SystemExit(f"合計 {total} 件は指定範囲（500〜1,000）の外です")


def main() -> None:
    cases = build_all()
    verify(cases)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)

        for case in cases:
            writer.writerow(
                [
                    case.case_id,
                    case.axis,
                    case.role,
                    case.role_label,
                    case.persona_id,
                    case.persona_name,
                    case.persona_profile,
                    case.viewpoint_id,
                    case.viewpoint,
                    case.title,
                    case.precondition,
                    case.steps_text,
                    case.expected_text,
                    case.user_value,
                    case.priority,
                    json.dumps(case.exec_spec, ensure_ascii=False),
                ]
            )

    role_cases = sum(1 for case in cases if case.axis == "role")
    print(f"{OUTPUT} へ {len(cases)} 件を書き出しました")
    print(f"  ロール別: {role_cases} 件（{len(ROLES)} ロール × {len(VIEWPOINTS)} 観点）")
    print(f"  ペルソナ別: {len(cases) - role_cases} 件（{len(PERSONAS)} ペルソナ × {len(STAGES)} 場面）")


if __name__ == "__main__":
    main()
