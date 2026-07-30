"""インデックス再構築コマンド。

    python manage.py rebuild_index --tenant acme
    python manage.py rebuild_index --tenant acme --project atlas
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Tenant
from apps.projects.models import Project
from apps.rag.models import IndexScope, VectorIndex
from apps.rag.services.indexer import rebuild_index


class Command(BaseCommand):
    help = "指定テナント（および案件）の検索インデックスを再構築します。"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", required=True, help="テナントコード")
        parser.add_argument("--project", help="案件コード。省略時はテナント共通インデックス。")

    def handle(self, *args, **options) -> None:
        tenant = Tenant.objects.filter(code=options["tenant"]).first()

        if tenant is None:
            raise CommandError(f"テナントが見つかりません: {options['tenant']}")

        project = None

        if options["project"]:
            project = Project.objects.filter(tenant=tenant, code=options["project"]).first()

            if project is None:
                raise CommandError(f"案件が見つかりません: {options['project']}")

        index, _ = VectorIndex.objects.get_or_create(
            tenant=tenant,
            project=project,
            defaults={"scope": IndexScope.PROJECT if project else IndexScope.TENANT},
        )
        result = rebuild_index(index)

        self.stdout.write(
            self.style.SUCCESS(
                f"インデックス構築完了: 文書 {result.document_count} 件 / "
                f"チャンク {result.chunk_count} 件 / "
                f"モデル {index.embedding_model} (dim={index.dimension})"
            )
        )
