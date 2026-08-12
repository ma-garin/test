"""PMO 自律運用化の実装契約が壊れていないことを検証する。"""

from __future__ import annotations

import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from tools.pmo_autopilot_harness.contracts import (
    QUEUE_PATH,
    ContractError,
    load_repository_contract,
    validate_package,
)


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


class QueueJsonIsolationTests(TestCase):
    """SEC-02: 実装エージェントが docs/agent/pmo_autopilot_queue.json を直接
    書き換えて完了を偽装しようとしても、apps.pmo_automation の実データ
    （Authority側の実際の状態）は一切変わらないことを検証する。

    queue.json は「実装作業の進行管理」だけを持つファイルであり、
    PmoWorkItem 等の実データとはコード上どこにも結び付いていない
    （import・FK参照が存在しない）。

    レビュー指摘: in-memory dict の書換えだけでは「ファイルに保存して
    いないから当然変わらない」という自明な検証にしかならない。ここでは
    実際に docs/agent/pmo_autopilot_queue.json ファイル自体を一時的に
    書き換え・再読込した上でDB側が無傷であることを確認する（必ず
    tearDown で元の内容へ復元する）。
    """

    def setUp(self) -> None:
        self._original_queue_json = QUEUE_PATH.read_text(encoding="utf-8")
        self.addCleanup(lambda: QUEUE_PATH.write_text(self._original_queue_json, encoding="utf-8"))

    def test_queue_jsonファイルを実際に書き換えてもpmo_automationの状態に影響しない(self) -> None:
        import json

        from apps.accounts.models import Tenant
        from apps.pmo_automation.models import PmoWorkItem, WorkItemState, WorkKind
        from apps.projects.models import Project

        tenant = Tenant.objects.create(code="acme", name="ACME")
        project = Project.objects.create(tenant=tenant, code="p1", name="基幹刷新")
        work_item = PmoWorkItem.objects.create(
            tenant=tenant,
            project=project,
            kind=WorkKind.DETECTION_TRIAGE,
            source_type="alert",
            source_key="sec02",
            dedupe_key="sec02:1",
            state=WorkItemState.AWAITING_APPROVAL,
        )

        queue_data = json.loads(self._original_queue_json)
        pa11 = next(ticket for ticket in queue_data["tickets"] if ticket["id"] == "PA-11")
        pa11["state"] = "done"
        pa11["executor"] = "attacker"
        pa11["evidence"] = ["fake-evidence"]
        pa11["review"] = {"reviewer": "someone-else", "passed": pa11["required_reviews"], "failed": []}
        # 実際にファイルへ書き込む（setUpのaddCleanupが必ず元へ戻す）。
        QUEUE_PATH.write_text(json.dumps(queue_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # PA-11はD-04（人の決定事項）が未決のためdecision_requiredでなければ
        # ならない契約になっている。review記録を偽装してdoneへ書き換えても、
        # このD-04未決チェックにより load_repository_contract（cli validate相当）
        # 自体が拒否する。「無関係だから変わらない」だけでなく「不正な改変は
        # 検証で弾かれる」という二重の安全性を示す。
        with self.assertRaises(ContractError):
            load_repository_contract()

        work_item.refresh_from_db()
        self.assertEqual(
            work_item.state,
            WorkItemState.AWAITING_APPROVAL,
            "queue.jsonファイルの実際の書換えが、PmoWorkItemの状態に影響してはならない。",
        )
