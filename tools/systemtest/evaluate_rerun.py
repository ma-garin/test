"""NG 再実行の結果と動画から、検証評価をまとめる。

    python tools/systemtest/evaluate_rerun.py --evidence docs/systemtest/evidence

`rerun-result.json` を読み、`evaluation.md` を作る。

**判定と証跡を別々に評価する**

「OK が出た」と「OK だと確かめられる」は別のこと。動画が 0 バイトだったり
スクリーンショットが欠けていたりすれば、判定が正しくても後から検証できない。
ここでは次の 2 つを分けて出す。

- **判定** … 期待どおりの応答が返ったか
- **証跡** … 動画とスクリーンショットが実際に残っているか

どちらかが欠けているケースは「確認済み」と呼ばない。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

#: これ未満の動画は、実質何も写っていないとみなす。
MIN_VIDEO_BYTES = 1024


def evidence_state(evidence_dir: Path, case: dict) -> tuple[bool, str]:
    """証跡が検証に使える状態か。"""

    problems: list[str] = []
    video = case.get("video") or ""

    if not video:
        problems.append("動画が残っていない")
    else:
        path = evidence_dir / video

        if not path.exists():
            problems.append("動画ファイルが見つからない")
        elif path.stat().st_size < MIN_VIDEO_BYTES:
            problems.append(f"動画が小さすぎる（{path.stat().st_size} バイト）")

    shots_dir = evidence_dir / (case.get("screenshots_dir") or "")
    shots = sorted(shots_dir.glob("*.png")) if shots_dir.is_dir() else []

    if not shots:
        problems.append("スクリーンショットが残っていない")

    return (not problems), "・".join(problems) if problems else f"動画1本 / 画面{len(shots)}枚"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default="docs/systemtest/evidence")
    args = parser.parse_args()

    evidence_dir = Path(args.evidence).resolve()
    result = json.loads((evidence_dir / "rerun-result.json").read_text(encoding="utf-8"))
    cases = result["cases"]

    rows = []
    verified = 0

    for case in cases:
        ok = case["verdict"] == "OK"
        has_evidence, evidence_note = evidence_state(evidence_dir, case)

        if ok and has_evidence:
            verified += 1

        rows.append(
            {
                "case_id": case["case_id"],
                "title": case["title"],
                "role": case["role"],
                "verdict": case["verdict"],
                "reason": case["reason"],
                "user_value": case["user_value"],
                "evidence_ok": has_evidence,
                "evidence_note": evidence_note,
                "video": case.get("video", ""),
                "steps": case.get("steps", []),
            }
        )

    ng = [row for row in rows if row["verdict"] != "OK"]
    no_evidence = [row for row in rows if not row["evidence_ok"]]

    lines = [
        "# NG ケース再実行の検証評価",
        "",
        "`tools/systemtest/evaluate_rerun.py` が生成する。",
        "",
        "システムテストで NG になったケースを、修整後に**実ブラウザで再実行**した結果。",
        "1 ケース 1 本の動画と、手順ごとのスクリーンショットを撮りながら実行している。",
        "",
        "## 集計",
        "",
        "| 項目 | 件数 |",
        "|---|---:|",
        f"| 再実行したケース | {len(rows)} |",
        f"| 判定 OK | {len(rows) - len(ng)} |",
        f"| 判定 NG | {len(ng)} |",
        f"| 証跡が揃っていないケース | {len(no_evidence)} |",
        f"| **確認済み（判定 OK かつ証跡あり）** | **{verified}** |",
        "",
        "「OK が出た」と「OK だと確かめられる」は別のこと。判定が正しくても動画や",
        "スクリーンショットが残っていなければ、後から検証できない。両方そろったものだけを",
        "「確認済み」と数えている。",
        "",
    ]

    if ng:
        lines += [
            "## まだ NG のケース",
            "",
            "| ケース | 役割 | 内容 | 失敗内容 |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| `{row['case_id']}` | {row['role']} | {row['title']} | {row['reason']} |" for row in ng
        ]
        lines.append("")
    else:
        lines += [
            "## まだ NG のケース",
            "",
            "無し。再実行したケースはすべて期待どおりの応答になった。",
            "",
        ]

    if no_evidence:
        lines += [
            "## 証跡が揃っていないケース",
            "",
            "| ケース | 不足 |",
            "|---|---|",
        ]
        lines += [f"| `{row['case_id']}` | {row['evidence_note']} |" for row in no_evidence]
        lines.append("")

    lines += [
        "## ケース別の評価",
        "",
        "| ケース | 役割 | 内容 | 判定 | 証跡 | 利用者にとっての価値 |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| `{row['case_id']}` | {row['role']} | {row['title']} | "
        f"{'OK' if row['verdict'] == 'OK' else 'NG'} | {row['evidence_note']} | {row['user_value']} |"
        for row in rows
    ]

    lines += [
        "",
        "## 動画から確かめたこと",
        "",
        "動画は「HTTP のやり取りだけでは写らない壊れ方」を見るために撮っている。",
        "再実行では次を目視できる形にしてある。",
        "",
        "- ログイン画面から実際に入り、対象の画面まで自分で辿り着けること",
        "- POST は JavaScript でフォームを組んで**ブラウザから実際に送信**しており、",
        "  遷移先の画面がそのまま動画に写ること（裏から投げると証拠にならない）",
        "- 権限が無い操作では、押した結果として拒否の画面へ遷移すること",
        "  （ボタンを隠しただけの「見た目の権限」ではないこと）",
        "",
        f"動画: `{evidence_dir.name}/videos/<ケースID>/*.webm`",
        f"画面: `{evidence_dir.name}/screenshots/<ケースID>/*.png`",
        "",
    ]

    (evidence_dir / "evaluation.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"再実行 {len(rows)} 件 / 判定OK {len(rows) - len(ng)} 件 / 確認済み {verified} 件")
    print(f"評価: {evidence_dir / 'evaluation.md'}")


if __name__ == "__main__":
    main()
