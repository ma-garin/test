"""Confluence ページの取込（外部 → 内部の文書台帳）。

RAG の対象が手動アップロードだけだと、設計書が更新されても索引は古いまま残る。
ここは Confluence のページを `Document` + `DocumentPage` として登録し、
RAG が常に最新の設計・議事録を引ける状態にするための経路。

守っている約束（`sync.py` と同じ）:

- **全体を 1 トランザクションにしない。** 1 件の失敗で全部巻き戻ると、
  1 ページでも壊れていると永久に取り込めない。1 件ずつ閉じる。
- **冪等。** `SyncedRecord` を `external_id`（Confluence のページID）で突き合わせ、
  `fingerprint` が変わっていなければ書き込まない。何度流しても Document は増えない。
- **片方向。** Confluence へは書かない。

備考（既存モデルを変更せずに収めるための割り切り。いずれも `apps/integrations/models.py`
と `apps/documents/models.py` の変更が必要になるため、本対応では行わない）:

1. `SyncedRecord.EntityType` に「文書」が無い。値を新設せず `ISSUE` を
   「外部から取り込んだ実体」の総称として借用し、`object_id` に `Document` の PK を入れる。
   provider が `confluence` の `SyncedRecord` は文書を指す、という前提で読むこと。
   TODO(親タスク): `EntityType.DOCUMENT` が追加されたらそちらへ移す。
2. `FileType` にも wiki ページに当たる種別が無い。書式付き文書として最も近い
   `DOCX` を借用し、出典は `Document.source_note` に必ず残す（Confluence 由来だと追える）。
3. ファイル実体は Confluence 側にある。`Document.file` には参照用の仮想パスだけを置き、
   本文は `DocumentPage.content` に持つ（`seed_demo` と同じ扱い）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from apps.documents.models import Document, DocumentPage, DocumentStatus, FileType
from apps.integrations.models import Connection, SyncedRecord, SyncJob
from apps.integrations.services.connectors import get_connector
from apps.integrations.services.connectors.base import ConnectorError
from apps.integrations.services.connectors.confluence import PROVIDER_CONFLUENCE, ExternalPage

#: `detail` に残す明細の上限。失敗が大量にあるときに履歴画面が開けなくなるのを避ける。
MAX_DETAIL_ROWS = 50

CREATED = "created"
UPDATED = "updated"
SKIPPED = "skipped"

#: モデル側のフィールド長。超過分は切って保存する（1 件の長さで取込全体を止めない）。
MAX_TITLE = 300
MAX_SOURCE_NOTE = 300
MAX_KEY = 120
MAX_URL = 200
MAX_SECTION_LABEL = 200

#: 備考 2 の割り切り。Confluence ページに対応する種別が `FileType` に無いため借用する。
PAGE_FILE_TYPE = FileType.DOCX

#: 文書を指す種別。`EntityType.DOCUMENT` の追加により借用は解消した。
PAGE_ENTITY_TYPE = SyncedRecord.EntityType.DOCUMENT


@dataclass(frozen=True)
class PageOutcome:
    """1 ページの取込結果。"""

    kind: str
    document_id: str = ""


def run_confluence_pull(connection: Connection, user=None) -> SyncJob:
    """Confluence のページを文書台帳へ取り込み、実行履歴（`SyncJob`）を返す。

    取込前の失敗（資格情報なし・スペース未設定等）も履歴に残す。例外で終わらせると
    「実行したのに何も残らない」状態になり、利用者が原因を追えない。
    """

    job = SyncJob.objects.create(
        connection=connection,
        direction=SyncJob.Direction.PULL,
        status=SyncJob.Status.RUNNING,
        started_at=timezone.now(),
        triggered_by=user if user is not None and user.is_authenticated else None,
    )

    try:
        _ensure_pullable(connection)
        connector = get_connector(connection)
        fetch_pages = getattr(connector, "fetch_pages", None)

        if not callable(fetch_pages):
            raise ConnectorError(
                "この接続はページの取込に対応していません。連携先が Confluence か確認してください"
            )

        pages = list(fetch_pages())
    except ConnectorError as error:
        # コネクタ側が利用者向けに書いた文言。そのまま見せてよい。
        return _fail(job, str(error))
    except Exception as error:  # noqa: BLE001 - 想定外でも履歴を残して終える
        # 想定外の例外は本文を出さない。URL やヘッダに資格情報が混ざる可能性があるため、
        # 種別だけを残す（詳細はサーバー側のログで追う）。
        return _fail(job, f"取込中に想定外のエラーが発生しました（{error.__class__.__name__}）")

    return _apply(job, connection, pages)


def _ensure_pullable(connection: Connection) -> None:
    """取り込める接続かを先に判定する。理由を利用者の言葉で返す。"""

    if not connection.is_active:
        raise ConnectorError("この接続は無効になっています。有効にしてから同期してください")

    if connection.provider != PROVIDER_CONFLUENCE:
        raise ConnectorError(
            "この接続は Confluence ではありません。文書の取込は Confluence 連携でのみ行えます"
        )

    if connection.tenant_id is None:  # pragma: no cover - モデル上は必須
        raise ConnectorError("取込先のテナントが特定できません")


def _apply(job: SyncJob, connection: Connection, pages: Iterable[ExternalPage]) -> SyncJob:
    """取得済みのページを 1 件ずつ文書台帳へ反映する。"""

    counts = {CREATED: 0, UPDATED: 0, SKIPPED: 0}
    failures: list[dict] = []
    fetched = 0

    for page in pages:
        fetched += 1

        try:
            # 1 件ずつトランザクションを閉じる。全体を 1 つにすると、
            # 1 ページの失敗で取込済みの全件が巻き戻ってしまう。
            with transaction.atomic():
                outcome = _sync_one(connection, page)
        except Exception as error:  # noqa: BLE001 - 1 件の失敗で残りを止めない
            failures.append(
                {
                    "page_id": page.page_id,
                    "title": page.title,
                    "reason": f"{error.__class__.__name__}: {error}",
                }
            )
            continue

        counts[outcome.kind] += 1

    return _finish(job, connection, counts=counts, failures=failures, fetched=fetched)


def _sync_one(connection: Connection, page: ExternalPage) -> PageOutcome:
    """1 ページを突き合わせて作成・更新・スキップのいずれかを行う。"""

    record = SyncedRecord.objects.filter(
        connection=connection, external_id=page.page_id
    ).first()

    document: Document | None = None

    if record is not None:
        # 内部側が消えている（論理削除された等）場合がある。対応表だけ残っていると
        # 「変更なしなのに文書が無い」状態が続くため、作り直す。
        document = Document.objects.filter(pk=record.object_id, deleted_at__isnull=True).first()

    if record is not None and document is not None and record.fingerprint == page.fingerprint:
        record.last_synced_at = timezone.now()
        record.save(update_fields=["last_synced_at", "updated_at"])

        return PageOutcome(kind=SKIPPED, document_id=str(document.pk))

    fields = _build_fields(connection, page)

    if document is None:
        document = Document(tenant=connection.tenant, project=connection.project, **fields)
        kind = CREATED
    else:
        for name, value in fields.items():
            setattr(document, name, value)

        kind = UPDATED

    document.save()

    # 本文は `DocumentPage` に持つ。`section_label` は固定にする。
    # 見出しをラベルにすると、ページ名の変更だけで別ページ扱いになり本文が二重に残る。
    DocumentPage.objects.update_or_create(
        document=document,
        page_number=1,
        section_label="",
        defaults={"content": page.body_text},
    )

    SyncedRecord.objects.update_or_create(
        connection=connection,
        external_id=page.page_id,
        defaults={
            "external_key": (f"{page.space_key}/{page.page_id}" if page.space_key else page.page_id)[
                :MAX_KEY
            ],
            "external_url": (page.url or "")[:MAX_URL],
            "entity_type": PAGE_ENTITY_TYPE,
            "object_id": document.pk,
            "fingerprint": page.fingerprint,
            "last_synced_at": timezone.now(),
        },
    )

    return PageOutcome(kind=kind, document_id=str(document.pk))


def _build_fields(connection: Connection, page: ExternalPage) -> dict:
    """Confluence ページを `Document` のフィールドへ写す。"""

    title = (page.title or f"Confluence ページ {page.page_id}")[:MAX_TITLE]
    body_bytes = page.body_text.encode("utf-8")
    source = f"Confluence {page.space_key}/{page.page_id}".strip()

    if page.url:
        source = f"{source} {page.url}"

    return {
        "title": title,
        # ファイル実体は Confluence 側にある。参照用の仮想パスだけを置く。
        "file": f"confluence/{connection.tenant.code}/{page.space_key or 'space'}/{page.page_id}.txt",
        "file_type": PAGE_FILE_TYPE,
        "file_size": len(body_bytes),
        "sha256": page.content_sha256,
        "source_note": source[:MAX_SOURCE_NOTE],
        # 取込直後は「RAG対象だが未インデックス」。索引構築は別ジョブの責務。
        "status": DocumentStatus.ACTIVE,
        "last_indexed_at": None,
    }


def _finish(
    job: SyncJob,
    connection: Connection,
    *,
    counts: dict[str, int],
    failures: list[dict],
    fetched: int,
) -> SyncJob:
    """件数と状態を確定して履歴へ書く。"""

    failed = len(failures)
    succeeded = counts[CREATED] + counts[UPDATED] + counts[SKIPPED]

    if failed == 0:
        status = SyncJob.Status.SUCCEEDED
    elif succeeded == 0:
        status = SyncJob.Status.FAILED
    else:
        status = SyncJob.Status.PARTIAL

    now = timezone.now()
    job.status = status
    job.created_count = counts[CREATED]
    job.updated_count = counts[UPDATED]
    job.skipped_count = counts[SKIPPED]
    job.failed_count = failed
    job.finished_at = now
    job.message = (
        f"{fetched} 件のページを取得: 新規 {counts[CREATED]} / 更新 {counts[UPDATED]} / "
        f"変更なし {counts[SKIPPED]} / 失敗 {failed}"
    )
    job.detail = {
        "fetched": fetched,
        "failures": failures[:MAX_DETAIL_ROWS],
        "truncated": len(failures) > MAX_DETAIL_ROWS,
    }
    job.save(
        update_fields=[
            "status",
            "created_count",
            "updated_count",
            "skipped_count",
            "failed_count",
            "finished_at",
            "message",
            "detail",
            "updated_at",
        ]
    )

    # 取得自体は成功しているので、最終同期は更新する。ここを飛ばすと
    # 「一部失敗が続く接続」がいつまでも未同期に見える。
    connection.last_synced_at = now
    connection.save(update_fields=["last_synced_at", "updated_at"])

    return job


def _fail(job: SyncJob, message: str) -> SyncJob:
    """取込前に終わった場合の記録。`last_synced_at` は更新しない。"""

    job.status = SyncJob.Status.FAILED
    job.finished_at = timezone.now()
    job.message = message
    job.detail = {"error": message}
    job.save(update_fields=["status", "finished_at", "message", "detail", "updated_at"])

    return job
