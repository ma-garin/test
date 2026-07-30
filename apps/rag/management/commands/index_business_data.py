"""業務データの取り込みコマンド。

    python manage.py index_business_data --tenant acme
    python manage.py index_business_data --tenant acme --project atlas

差分更新なので、定期実行しても更新されたレコードだけが再ベクトル化される。
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Tenant
from apps.projects.models import Project
from apps.rag.services.business_indexer import ensure_tenant_index, index_business_data


class Command(BaseCommand):
    help = "課題・不具合・リスク・変更要求・WBSタスクを検索インデックスへ取り込みます。"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", required=True, help="テナントコード")
        parser.add_argument("--project", help="案件コード。省略時はテナント配下の全案件。")

    def handle(self, *args, **options) -> None:
        tenant = Tenant.objects.filter(code=options["tenant"]).first()

        if tenant is None:
            raise CommandError(f"テナントが見つかりません: {options['tenant']}")

        project = None

        if options["project"]:
            # テナント条件を必ず含める。案件コードだけで引くとテナント越境になる。
            project = Project.objects.alive().filter(tenant=tenant, code=options["project"]).first()

            if project is None:
                raise CommandError(f"案件が見つかりません: {options['project']}")

        result = index_business_data(ensure_tenant_index(tenant), project=project)

        self.stdout.write(
            self.style.SUCCESS(
                f"業務データ取込完了: 新規 {result.created} 件 / 更新 {result.updated} 件 / "
                f"変更なし {result.unchanged} 件 / 削除 {result.deleted} 件 "
                f"（インデックス総チャンク {result.index.chunk_count} 件）"
            )
        )
