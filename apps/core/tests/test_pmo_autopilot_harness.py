"""PMO 自律運用化の実装契約が壊れていないことを検証する。"""

from __future__ import annotations

import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase

from tools.pmo_autopilot_harness.contracts import ContractError, load_repository_contract, validate_package


class PmoAutopilotHarnessTests(SimpleTestCase):
    # test_migrationに未反映のモデル変更がない は makemigrations --check が
    # 適用済みmigrationの整合性をDBへ問い合わせるため、default DB接続を許可する。
    databases = {"default"}

    def test_repository_contract_is_consistent(self):
        package = load_repository_contract()
        self.assertEqual(len(package["queue"]["tickets"]), 13)
        self.assertEqual(len(package["scenarios"]["scenarios"]), 14)

    def test_p0_external_safety_cannot_be_removed(self):
        package = load_repository_contract()
        package["contract"]["forbidden_in_p0"].remove("未承認の外部システム書き込み")
        with self.assertRaises(ContractError):
            validate_package(package)

    def test_unresolved_decision_blocks_its_ticket(self):
        package = load_repository_contract()
        pa11 = next(ticket for ticket in package["queue"]["tickets"] if ticket["id"] == "PA-11")
        pa11["state"] = "untouched"
        with self.assertRaises(ContractError):
            validate_package(package)

    def test_required_scenario_must_point_back_to_ticket(self):
        package = load_repository_contract()
        h01 = next(scenario for scenario in package["scenarios"]["scenarios"] if scenario["id"] == "H-01")
        h01["ticket_id"] = "PA-02"
        with self.assertRaises(ContractError):
            validate_package(package)

    def test_実装者自身のreviewを持つdoneチケットは拒否される(self):
        """安全施策.md SC-02: reviewer を自由文字列で受けると自己レビューを
        偽装できる、という懸念に対する静的検証（レビュー指摘対応）。"""

        package = load_repository_contract()
        pa01 = next(ticket for ticket in package["queue"]["tickets"] if ticket["id"] == "PA-01")
        pa01["review"]["reviewer"] = pa01["executor"]
        with self.assertRaises(ContractError):
            validate_package(package)

    def test_failed項目が残るreviewを持つdoneチケットは拒否される(self):
        package = load_repository_contract()
        pa01 = next(ticket for ticket in package["queue"]["tickets"] if ticket["id"] == "PA-01")
        pa01["review"]["failed"] = [pa01["required_reviews"][0]]
        with self.assertRaises(ContractError):
            validate_package(package)

    def test_review記録の無いdoneチケットは拒否される(self):
        package = load_repository_contract()
        pa01 = next(ticket for ticket in package["queue"]["tickets"] if ticket["id"] == "PA-01")
        del pa01["review"]
        with self.assertRaises(ContractError):
            validate_package(package)

    def test_executorキーを削除して自己レビュー検知を回避することはできない(self):
        """executorキーごと削除すると reviewer != executor(None) が常に真になり、
        自己レビュー検知を素通りできてしまう穴を塞ぐ（レビュー指摘対応）。"""

        package = load_repository_contract()
        pa01 = next(ticket for ticket in package["queue"]["tickets"] if ticket["id"] == "PA-01")
        del pa01["executor"]
        with self.assertRaises(ContractError):
            validate_package(package)

    def test_executorを空白文字列にして自己レビュー検知を回避することはできない(self):
        """空白のみの executor でも reviewer との不一致判定を迂回できてしまう
        穴を塞ぐ（レビュー指摘対応、前回修正の亜種）。"""

        package = load_repository_contract()
        pa01 = next(ticket for ticket in package["queue"]["tickets"] if ticket["id"] == "PA-01")
        pa01["executor"] = "   "
        with self.assertRaises(ContractError):
            validate_package(package)

    def test_loader_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for name in (
                "pmo_autopilot_contract.json",
                "pmo_autopilot_decisions.json",
                "pmo_autopilot_scenarios.json",
                "pmo_autopilot_queue.json",
            ):
                (directory / name).write_text("{}", encoding="utf-8")
            (directory / "pmo_autopilot_queue.json").write_text("{", encoding="utf-8")
            with self.assertRaises(ContractError):
                load_repository_contract(directory)

    def test_全required_scenarioがdoneチケットのevidenceに紐付く(self):
        """PA-10 受入条件: H-01からH-14が全て実テストに紐付く。

        ここでの「紐付く」は、対応チケットが done であり、かつ
        required_scenario の ID が evidence として記録されていることで表す。
        """

        package = load_repository_contract()
        scenario_ids = {scenario["id"] for scenario in package["scenarios"]["scenarios"]}
        evidence_ids: set[str] = set()
        for ticket in package["queue"]["tickets"]:
            evidence_ids.update(ticket.get("evidence", []))

        missing = scenario_ids - evidence_ids
        self.assertEqual(missing, set(), f"evidence未記録のシナリオ: {sorted(missing)}")

    def test_migrationに未反映のモデル変更がない(self):
        """PA-10 受入条件: migration一貫性をリリースゲートへ含める。

        `makemigrations --check --dry-run` は、モデル変更に対応する migration が
        無ければ非ゼロで終了する（SystemExit）。ここでは例外が飛ばないこと、
        つまり未反映の変更が無いことを検証する。
        """

        out = StringIO()
        try:
            call_command("makemigrations", "--check", "--dry-run", stdout=out, stderr=out)
        except SystemExit as error:
            self.fail(f"未反映の migration があります（makemigrations が必要）: {out.getvalue()}\n{error}")
