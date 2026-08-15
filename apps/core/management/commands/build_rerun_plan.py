"""NG ケースの再実行計画を組み立て、ブラウザ用の DB を用意する。

    python manage.py build_rerun_plan --out var/systemtest/rerun-plan.json

やること は 2 つ。

1. いま接続している DB へユースケース用の世界（体験データ + ロール別利用者）を作る
2. NG になったケースの手順を、URL 名から実パス・実 ID・実ペイロードへ解決して
   JSON へ書き出す

ブラウザ側（`tools/systemtest/rerun_with_video.py`）は Django を知らなくてよくなる。
URL 名の解決やフィクスチャの ID をブラウザ側へ二重実装すると、必ず食い違う。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from django.core.management.base import BaseCommand
from django.urls import reverse

BASE_DIR = Path(__file__).resolve().parents[4]
# 再実行の対象は「修整前に NG だったケース」。修整後の結果（全部 OK）から
# 作ると対象が空になり、直ったことを確かめられない。修整前の結果は
# baseline/ に残してあるので、そこを既定にする。
RESULTS_DIR = BASE_DIR / "docs" / "systemtest" / "results" / "baseline"
CSV_PATH = BASE_DIR / "docs" / "systemtest" / "usecases" / "usecases.csv"

sys.path.insert(0, str(BASE_DIR / "tools" / "systemtest"))


class Command(BaseCommand):
    help = "NGケースの再実行計画を作り、ブラウザ用DBへフィクスチャを投入する"

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="計画JSONの出力先")
        parser.add_argument(
            "--results", default=str(RESULTS_DIR), help="NGを拾う結果JSONのディレクトリ"
        )
        parser.add_argument("--csv", default=str(CSV_PATH))
        parser.add_argument("--limit", type=int, default=0, help="先頭N件に絞る（0で全件）")

    def handle(self, *args, **options):
        import csv as csv_module

        # 世界の作り方と URL 名の解決は実行ハーネスと同じものを使う。
        # ブラウザ側へ二重実装すると、フィクスチャの ID が必ず食い違う。
        from apps.core.management.commands.run_usecases import build_payload, build_world, resolve

        ng_ids = self._collect_ng(Path(options["results"]))

        if not ng_ids:
            self.stdout.write("NG ケースがありません。再実行は不要です。")
            Path(options["out"]).parent.mkdir(parents=True, exist_ok=True)
            Path(options["out"]).write_text(json.dumps({"cases": []}, ensure_ascii=False), "utf-8")

            return

        with Path(options["csv"]).open(encoding="utf-8-sig", newline="") as handle:
            rows = {row["case_id"]: row for row in csv_module.DictReader(handle)}

        world = build_world()
        plan = []

        for case_id in ng_ids:
            row = rows.get(case_id)

            if row is None:
                continue

            spec = json.loads(row["exec_spec"])
            steps = []

            for step in spec["steps"]:
                payload = build_payload(step, world)
                # ファイルは JSON へ入らない。ブラウザ側では扱わず、手順から外す。
                has_upload = any(hasattr(value, "read") for value in payload.values())
                steps.append(
                    {
                        "method": step["m"],
                        "url_name": step["u"],
                        "path": reverse(
                            step["u"], args=[resolve(value, world) for value in step.get("args", [])]
                        ),
                        "query": step.get("query") or {},
                        "data": {
                            key: value for key, value in payload.items() if not hasattr(value, "read")
                        },
                        "expected_status": step.get("expect", {}).get("status") or [200],
                        "skip_in_browser": has_upload,
                        "note": "ファイル添付を伴うためブラウザ再実行では送信しない" if has_upload else "",
                    }
                )

            plan.append(
                {
                    "case_id": case_id,
                    "title": row["title"],
                    "role": row["role"],
                    "email": f"uc-{row['role']}@example.com",
                    "expected_text": row["expected_text"],
                    "user_value": row["user_value"],
                    "steps": steps,
                }
            )

            if options["limit"] and len(plan) >= options["limit"]:
                break

        out = Path(options["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"cases": plan}, ensure_ascii=False, indent=2), encoding="utf-8")

        self.stdout.write(f"{len(plan)} 件の再実行計画を {out} へ書き出しました")

    def _collect_ng(self, results_dir: Path) -> list[str]:
        ng: list[str] = []

        for path in sorted(results_dir.glob("result-*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))

            ng.extend(case["case_id"] for case in data["cases"] if case["verdict"] == "NG")

        # 同じケースが複数ファイルに出ることは無いが、念のため順序を保って重複を除く。
        return list(dict.fromkeys(ng))
