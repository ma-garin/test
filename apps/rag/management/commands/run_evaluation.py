"""RAG 評価の実行コマンド。

    python manage.py run_evaluation --tenant acme --suite retrieval
    python manage.py run_evaluation --tenant acme --suite retrieval_offline
    python manage.py run_evaluation --tenant acme --suite answer
    python manage.py run_evaluation --tenant acme --suite static

外部 API は呼ばない。CI から定期実行して劣化を検知することを想定している。
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Tenant
from apps.projects.models import Project
from apps.rag.models import EvaluationSuite
from apps.rag.services.evaluation import metric_deltas, run_evaluation


class Command(BaseCommand):
    help = "Golden Dataset に基づく RAG 評価を実行し、履歴として保存します。"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", required=True, help="テナントコード")
        parser.add_argument("--project", help="案件コード。省略時はテナント共通。")
        parser.add_argument(
            "--suite",
            default=EvaluationSuite.RETRIEVAL,
            choices=list(EvaluationSuite.values),
            help="評価スイート",
        )
        parser.add_argument("--top-k", type=int, dest="top_k", help="評価する上位件数")

    def handle(self, *args, **options) -> None:
        tenant = Tenant.objects.filter(code=options["tenant"]).first()

        if tenant is None:
            raise CommandError(f"テナントが見つかりません: {options['tenant']}")

        project = None

        if options["project"]:
            project = Project.objects.filter(tenant=tenant, code=options["project"]).first()

            if project is None:
                raise CommandError(f"案件が見つかりません: {options['project']}")

        run = run_evaluation(
            tenant=tenant,
            project=project,
            suite=options["suite"],
            top_k=options.get("top_k"),
        )

        self._report(run)

    def _report(self, run) -> None:
        self.stdout.write(f"評価スイート: {run.get_suite_display()} / 対象 {run.case_count} 件")

        if not run.evaluable:
            # 0 点と評価不能を混同させない。ここが一番の落とし穴。
            self.stdout.write(self.style.WARNING(f"評価不能: {run.unavailable_reason}"))
        else:
            for row in metric_deltas(run):
                if row.current is None:
                    continue

                diff = "" if row.delta is None else f"（前回比 {row.delta:+}{row.unit}）"
                self.stdout.write(f"  {row.label}: {row.current}{row.unit}{diff}")

        for issue in run.issues:
            self.stdout.write(self.style.WARNING(f"  ! {issue}"))

        if run.evaluable and not run.issues:
            self.stdout.write(self.style.SUCCESS("検出事項はありません。"))
