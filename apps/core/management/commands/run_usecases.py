"""ユースケース（システムテスト）を実行する。

    python manage.py run_usecases --settings=config.settings.test
    python manage.py run_usecases --role pmo --out docs/systemtest/results/pmo.json

`docs/systemtest/usecases/usecases.csv` の `exec_spec` を読み、専用のテスト用 DB を
作って実際に画面を叩く。既存の開発 DB は一切触らない。

**1 ケース 1 トランザクション**

ケースは互いに独立していなければ、あるケースの書き込みが次のケースの前提を変え、
落ちた理由が追えなくなる。各ケースをトランザクションで囲み、最後に必ず巻き戻す。

**「200 が返った」を成功と呼ばない**

権限が無いのに 200 を返す画面は、ボタンを隠しただけで実際には書き込めている
ことがある。書き込みを伴う手順では、対象モデルの件数の増減も併せて確かめる。
拒否されるべきケースでは、件数が 1 件でも増えていれば NG とする。
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction
from django.test import Client
from django.test.utils import setup_test_environment, teardown_test_environment
from django.urls import reverse

BASE_DIR = Path(__file__).resolve().parents[4]
DEFAULT_CSV = BASE_DIR / "docs" / "systemtest" / "usecases" / "usecases.csv"
DEFAULT_OUT = BASE_DIR / "docs" / "systemtest" / "results"

sys.path.insert(0, str(BASE_DIR / "tools" / "systemtest"))


class Command(BaseCommand):
    help = "ユースケースCSVを読み込み、Djangoテストクライアントで実行する"

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(DEFAULT_CSV), help="ユースケースCSVのパス")
        parser.add_argument("--role", default="", help="このロールのケースだけ実行する")
        parser.add_argument("--case", default="", help="このケースIDだけ実行する（カンマ区切り可）")
        parser.add_argument("--out", default="", help="結果JSONの出力先")
        parser.add_argument("--keep-db", action="store_true", help="テストDBを残す")

    def handle(self, *args, **options):
        cases = self._load_cases(options["csv"], options["role"], options["case"])

        if not cases:
            raise SystemExit("実行対象のケースがありません")

        setup_test_environment()
        from django.db import connection

        old_name = connection.creation.create_test_db(verbosity=0, autoclobber=True)

        try:
            world = build_world()
            results = [self._run_case(case, world) for case in self._progress(cases)]
        finally:
            connection.creation.destroy_test_db(old_name, verbosity=0, keepdb=options["keep_db"])
            teardown_test_environment()

        self._report(results, options)

    # --- 読み込み -----------------------------------------------------------

    def _load_cases(self, path: str, role: str, case_ids: str) -> list[dict]:
        wanted = {value.strip() for value in case_ids.split(",") if value.strip()}

        with Path(path).open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))

        return [
            row
            for row in rows
            if (not role or row["role"] == role) and (not wanted or row["case_id"] in wanted)
        ]

    def _progress(self, cases):
        total = len(cases)

        for index, case in enumerate(cases, start=1):
            if index % 50 == 0 or index == total:
                self.stderr.write(f"  {index}/{total} 実行")

            yield case

    # --- 実行 ---------------------------------------------------------------

    def _run_case(self, case: dict, world: dict) -> dict:
        spec = json.loads(case["exec_spec"])
        started = time.perf_counter()
        steps_result: list[dict] = []
        verdict = "OK"
        reason = ""

        try:
            with transaction.atomic():
                client = Client()
                client.force_login(world["users"][case["role"]])

                for index, step in enumerate(spec["steps"], start=1):
                    outcome = self._run_step(client, step, world)
                    steps_result.append(outcome)

                    if outcome["verdict"] != "OK":
                        verdict = "NG"
                        reason = f"手順{index}: {outcome['reason']}"

                        break

                # ケース間を独立させる。書き込みは必ず巻き戻す。
                transaction.set_rollback(True)
        except Exception as error:  # noqa: BLE001 - 落ちたこと自体が結果
            verdict = "NG"
            reason = f"例外: {type(error).__name__}: {error}"[:400]

        return {
            "case_id": case["case_id"],
            "axis": case["axis"],
            "role": case["role"],
            "persona_id": case["persona_id"],
            "viewpoint_id": case["viewpoint_id"],
            "viewpoint": case["viewpoint"],
            "title": case["title"],
            "priority": case["priority"],
            "expected": case["expected_text"],
            "user_value": case["user_value"],
            "verdict": verdict,
            "reason": reason,
            "steps": steps_result,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    def _run_step(self, client: Client, step: dict, world: dict) -> dict:
        url = reverse(step["u"], args=[resolve(value, world) for value in step.get("args", [])])
        expect = step.get("expect", {})
        payload = build_payload(step, world)
        model = apps.get_model(step["effect"]) if step.get("effect") else None
        before = model.objects.count() if model else None

        if step["m"] == "GET":
            response = client.get(url, data=step.get("query") or {})
        else:
            response = client.post(url, data=payload)

        after = model.objects.count() if model else None
        allowed = expect.get("status") or [200]
        outcome = {
            "url_name": step["u"],
            "method": step["m"],
            "status": response.status_code,
            "expected_status": allowed,
            "verdict": "OK",
            "reason": "",
        }

        if response.status_code not in allowed:
            outcome["verdict"] = "NG"
            outcome["reason"] = (
                f"{step['m']} {step['u']} が {response.status_code} を返した"
                f"（期待 {allowed}）"
            )

            return outcome

        if model is not None:
            delta = after - before
            outcome["effect_delta"] = delta
            permitted = expect.get("permitted", True)

            if permitted and delta < 1:
                outcome["verdict"] = "NG"
                outcome["reason"] = (
                    f"{step['u']} は成功応答を返したが {step['effect']} が増えていない"
                    "（画面は通るのに記録が残らない）"
                )
            elif not permitted and delta != 0:
                outcome["verdict"] = "NG"
                outcome["reason"] = (
                    f"権限が無いのに {step['effect']} が {delta} 件増えた（権限の回避）"
                )

        return outcome

    # --- 出力 ---------------------------------------------------------------

    def _report(self, results: list[dict], options: dict) -> None:
        ng = [row for row in results if row["verdict"] == "NG"]
        out_dir = DEFAULT_OUT
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = options["role"] or "all"
        path = Path(options["out"]) if options["out"] else out_dir / f"result-{suffix}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"total": len(results), "ok": len(results) - len(ng), "ng": len(ng), "cases": results},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        self.stdout.write(f"実行 {len(results)} 件 / OK {len(results) - len(ng)} 件 / NG {len(ng)} 件")
        self.stdout.write(f"結果: {path}")

        for row in ng[:20]:
            self.stdout.write(f"  NG {row['case_id']} {row['title']} — {row['reason']}")


# --- フィクスチャ -----------------------------------------------------------


def resolve(value, world: dict):
    """`{project_id}` のような差し込みを実際の値へ置き換える。"""

    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        return world["ids"][value[1:-1]]

    return value


def build_payload(step: dict, world: dict) -> dict:
    data = {key: resolve(value, world) for key, value in (step.get("data") or {}).items()}

    if step.get("form"):
        data.update(world["forms"][step["form"]]())

    return data


def build_world() -> dict:
    """テスト用の世界を1回だけ作る。

    体験用データ（`seed_demo`）を土台にする。ユースケースは実データに近い状態で
    通らなければ意味がないため、空のDBに最小限のレコードを置く作り方はしない。
    """

    from datetime import date, timedelta

    from catalog import PROJECT_ROLE_OF, ROLES
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.core.management import call_command
    from django.utils import timezone

    from apps.accounts.constants import Role
    from apps.accounts.models import Tenant, User
    from apps.dashboard.models import Alert, InterventionProposal
    from apps.pmo.models import Deliverable
    from apps.projects.models import (
        ChangeRequest,
        Defect,
        Issue,
        Priority,
        Project,
        ProjectMember,
        Risk,
        Severity,
        WbsTask,
    )

    call_command("seed_demo", verbosity=0)

    tenant = Tenant.objects.order_by("created_at").first()
    project = Project.objects.filter(tenant=tenant).order_by("code").first()

    users = {}

    for role in ROLES:
        user = User.objects.create_user(
            username=f"uc-{role}",
            email=f"uc-{role}@example.com",
            password="usecase-password",
            tenant=tenant,
            role=role,
            display_name=f"UC {role}",
        )
        users[role] = user
        project_role = PROJECT_ROLE_OF[role]

        if project_role:
            ProjectMember.objects.create(
                project=project, user=user, role=project_role, role_label=project_role
            )

    task = WbsTask.objects.filter(project=project).order_by("wbs_code").first()
    issue = Issue.objects.filter(project=project).first()
    risk = Risk.objects.filter(project=project).first()
    defect = Defect.objects.filter(project=project).first()

    # 判断待ちの変更要求。判断済みのものを掴むと「もう決まっている」で弾かれ、
    # 権限の検証にならない。
    change = ChangeRequest.objects.filter(
        project=project, status__in=(ChangeRequest.Status.UNDER_REVIEW, ChangeRequest.Status.PENDING_APPROVAL)
    ).first() or ChangeRequest.objects.create(
        project=project,
        title="システムテスト用の変更要求",
        status=ChangeRequest.Status.PENDING_APPROVAL,
        requested_by="システムテスト",
        description="判断待ちの状態を作るために用意したもの",
    )

    alert = Alert.objects.filter(project=project).first() or Alert.objects.create(
        project=project,
        category=Alert.Category.SCHEDULE,
        severity=Alert.Severity.WARNING,
        title="システムテスト用のアラート",
        detected_at=timezone.now(),
    )
    proposal = InterventionProposal.objects.filter(
        project=project, status=InterventionProposal.Status.PROPOSED
    ).first() or InterventionProposal.objects.create(
        project=project,
        alert=alert,
        title="システムテスト用の介入提案",
        rationale="判断待ちの状態を作るために用意したもの",
        confidence=0.5,
        status=InterventionProposal.Status.PROPOSED,
    )

    deliverable = Deliverable.objects.filter(project=project).first() or Deliverable.objects.create(
        project=project,
        kind=Deliverable.Kind.OTHER,
        title="システムテスト用の成果物",
        status=Deliverable.Status.PENDING_APPROVAL,
        ai_generated_body="AI が下書きした本文",
        body="人が確認して確定した本文",
    )

    ids = {
        "tenant_id": str(tenant.pk),
        "project_id": str(project.pk),
        "task_id": str(task.pk) if task else "",
        "issue_id": str(issue.pk) if issue else "",
        "risk_id": str(risk.pk) if risk else "",
        "defect_id": str(defect.pk) if defect else "",
        "change_id": str(change.pk),
        "alert_id": str(alert.pk),
        "proposal_id": str(proposal.pk),
        "deliverable_id": str(deliverable.pk),
    }

    today = date.today()
    counter = {"n": 0}

    def unique(prefix: str) -> str:
        counter["n"] += 1

        return f"{prefix}-{counter['n']:04d}"

    forms = {
        "task": lambda: {
            "project": ids["project_id"],
            "wbs_code": unique("UC"),
            "name": "システムテストで作成したタスク",
            "owner": "システムテスト",
            "planned_start": today.isoformat(),
            "planned_end": (today + timedelta(days=7)).isoformat(),
            "progress_percent": "0",
            "priority": Priority.MEDIUM,
            "status": WbsTask.Status.NOT_STARTED,
            "follow_up_state": WbsTask.FollowUpState.NONE,
            "next_action": "",
            "ball_holder": "",
            "evidence_note": "",
        },
        "issue": lambda: {
            "project": ids["project_id"],
            "title": unique("システムテストで起票した課題"),
            "description": "ユースケーステストから起票",
            "status": Issue.Status.OPEN,
            "severity": Severity.MEDIUM,
            "owner": "システムテスト",
            "due_date": (today + timedelta(days=14)).isoformat(),
            "external_key": "",
        },
        "risk": lambda: {
            "project": ids["project_id"],
            "title": unique("システムテストで登録したリスク"),
            "description": "ユースケーステストから登録",
            "status": Risk.Status.IDENTIFIED,
            "impact": "3",
            "probability": "3",
            "mitigation": "対策を検討する",
            "owner": "システムテスト",
            "due_date": (today + timedelta(days=21)).isoformat(),
        },
        "defect": lambda: {
            "project": ids["project_id"],
            "title": unique("システムテストで登録した不具合"),
            "status": Defect.Status.NEW,
            "severity": Severity.MEDIUM,
            "phase": "結合テスト",
            "description": "ユースケーステストから登録",
            "detected_on": today.isoformat(),
            "closed_on": "",
        },
        "document": lambda: {
            "title": unique("システムテストで登録した文書"),
            "project": ids["project_id"],
            "source_note": "ユースケーステスト",
            "file": SimpleUploadedFile(
                f"{unique('usecase')}.pdf",
                b"%PDF-1.4\n% usecase test fixture\n",
                content_type="application/pdf",
            ),
        },
    }

    _ = Role  # 参照だけ（ロール定数の存在確認）

    return {"users": users, "ids": ids, "forms": forms}
