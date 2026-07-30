"""文書ファイルからの本文抽出（PDF / Office / テキスト）。

インシデント INCIDENT-001 で欠落が判明した最優先項目（traceability #55）。
文書は登録できても本文が無いためチャンクが作られず、実文書が RAG 検索に
一切出てこない状態だった。ここがその欠落を埋める。

設計の理由:

- 形式ごとの実装を分け、`EXTRACTORS` の辞書で振り分ける。新形式の追加が
  「関数 1 つ + 辞書 1 行」で済み、分岐が肥大化しない。
- 外部ライブラリは**遅延 import** する。未導入でもアプリは起動でき、画面も
  500 にならず、`IngestJob` に「何を入れれば動くか」を残せる。
- プレーンテキスト（.txt / .md）だけは外部依存ゼロにする。依存が入っていない
  環境でも「抽出 → チャンク化 → 検索」を端から端まで通せるようにするため。
- ページ（シート）単位で DB へ流し込む。上限 50MB のファイルを丸ごと文字列へ
  展開すると、同時実行でメモリを食い潰すため。
- 0 文字の抽出は**成功にしない**。画像だけの PDF は実務で頻出し、黙って空の
  インデックスを作ると「検索しても出てこない」原因が分からなくなる。
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.documents.models import (
    Document,
    DocumentPage,
    DocumentStatus,
    FileType,
    IngestJob,
)

#: DocumentPage をまとめて INSERT する単位。全ページを配列に溜めないための上限でもある。
PAGE_BATCH_SIZE = 50

#: ページの概念を持たない形式（テキスト・Word）を、何文字ごとに 1 ページ扱いにするか。
TEXT_PAGE_CHARS = 4_000

#: 1 ページに保持する最大文字数。巨大な Excel シート 1 枚でメモリを食わないための蓋。
MAX_PAGE_CHARS = 200_000

#: テキストの復号順。業務文書は UTF-8 か Shift_JIS 系が大半。
TEXT_ENCODINGS = ("utf-8", "cp932")

#: IngestJob.message の保存上限（TextField だが、例外文字列をそのまま貯めない）。
MESSAGE_MAX_LENGTH = 2_000


class ExtractionError(Exception):
    """本文抽出に失敗した。

    呼び出し側はこれを捕まえて `IngestJob` の失敗として記録する。画面へ例外を
    素通しさせないため、抽出中の異常はすべてこの型へ寄せる。
    """


@dataclass(frozen=True)
class ExtractedPage:
    """抽出された 1 ページ（Excel なら 1 シート）。

    `DocumentPage` と同じ形にしてある。保存前に加工を挟まないことで、
    「画面に出ている本文」と「インデックスされた本文」がずれないようにする。
    """

    page_number: int
    content: str
    section_label: str = ""


@dataclass(frozen=True)
class ExtractionResult:
    """抽出ジョブの結果。失敗しても理由を必ず持ち帰る。"""

    document: Document
    job: IngestJob
    page_count: int = 0
    char_count: int = 0
    source_page_count: int = 0
    index_job: IngestJob | None = None

    @property
    def succeeded(self) -> bool:
        return self.job.status == IngestJob.Status.SUCCEEDED

    @property
    def message(self) -> str:
        return self.job.message


def _require(module_name: str, package: str):
    """依存ライブラリを遅延 import する。

    未導入を「何を入れれば動くか」が分かるメッセージへ変換する。ここで
    ImportError を素通しすると画面が 500 になる。
    """

    try:
        return importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - 導入済み環境では通らない
        raise ExtractionError(
            f"この形式を扱うには {package} をインストールしてください"
            "（pip install -r requirements/ingest.txt）。"
        ) from exc


def _open_binary(document: Document):
    """文書の実体をバイナリで開く。

    ストレージ上にファイルが無い（旧台帳からの移行漏れ等）ケースを、例外では
    なく抽出失敗として扱えるようにする。
    """

    try:
        return document.file.open("rb")
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise ExtractionError(f"ファイルを開けません: {exc}") from exc


def _decode(raw: bytes) -> str:
    """バイト列を文字列へ。復号できない文字があっても抽出は止めない。

    文字化けは「読めない 1 文字」で済むが、例外にすると文書 1 件が丸ごと
    検索対象から落ちるため、置換にフォールバックする。
    """

    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    return raw.decode("utf-8", errors="replace")


def _paginate(lines: Iterator[str]) -> Iterator[ExtractedPage]:
    """ページの概念が無い形式を、文字数で疑似ページへ切る。

    行の反復子を受け取り、行単位で溜めて吐き出す。全文を配列へ持たない。
    """

    page_number = 1
    buffer: list[str] = []
    size = 0

    for line in lines:
        buffer.append(line)
        size += len(line)

        if size >= TEXT_PAGE_CHARS:
            yield ExtractedPage(page_number=page_number, content="".join(buffer))
            page_number += 1
            buffer = []
            size = 0

    if buffer:
        yield ExtractedPage(page_number=page_number, content="".join(buffer))


def _extract_plain_text(document: Document) -> Iterator[ExtractedPage]:
    """.txt / .md。外部依存ゼロで必ず動く経路。

    行単位で読むことで、数十 MB のログ・議事録でもメモリが一定に保たれる。
    """

    handle = _open_binary(document)

    try:
        yield from _paginate(_decode(raw_line) for raw_line in handle)
    except ExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 - 破損ファイルをジョブ失敗へ落とす
        raise ExtractionError(f"テキストを読み込めません: {exc}") from exc
    finally:
        handle.close()


def _extract_pdf(document: Document) -> Iterator[ExtractedPage]:
    """PDF。pypdf をページ単位で回す（全ページを一度に展開しない）。"""

    pypdf = _require("pypdf", "pypdf")
    handle = _open_binary(document)

    try:
        try:
            reader = pypdf.PdfReader(handle)

            # パスワード保護は「空パスワードで開けるか」を試し、駄目なら失敗として記録する。
            if getattr(reader, "is_encrypted", False) and not reader.decrypt(""):
                raise ExtractionError(
                    "パスワード保護された PDF です。保護を解除してから登録してください。"
                )

            page_iterator = enumerate(reader.pages, start=1)
        except ExtractionError:
            raise
        except Exception as exc:  # noqa: BLE001 - 破損 PDF
            raise ExtractionError(f"PDF を読み込めません（破損の可能性）: {exc}") from exc

        for number, page in page_iterator:
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001 - 特定ページだけ壊れている PDF
                raise ExtractionError(f"{number} ページ目を抽出できません: {exc}") from exc

            yield ExtractedPage(page_number=number, content=text)
    finally:
        handle.close()


def _sheet_lines(sheet) -> Iterator[str]:
    """Excel シートを 1 行ずつタブ区切りの文字列にする。"""

    for row in sheet.iter_rows(values_only=True):
        cells = [str(value) for value in row if value not in (None, "")]

        if cells:
            yield "\t".join(cells) + "\n"


def _extract_excel(document: Document) -> Iterator[ExtractedPage]:
    """Excel（.xlsx / .xlsm）。シート 1 枚を 1 ページとして扱う。

    `read_only=True` で行をストリーム読みし、`data_only=True` で数式ではなく
    計算結果を取る。検索対象にしたいのは数式ではなく値のため。
    """

    openpyxl = _require("openpyxl", "openpyxl")
    handle = _open_binary(document)
    workbook = None

    try:
        try:
            workbook = openpyxl.load_workbook(handle, read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - 破損・パスワード保護
            raise ExtractionError(
                f"Excel を読み込めません（破損またはパスワード保護の可能性）: {exc}"
            ) from exc

        for number, sheet in enumerate(workbook.worksheets, start=1):
            try:
                text = _join_capped(_sheet_lines(sheet))
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError(f"シート {number} を抽出できません: {exc}") from exc

            yield ExtractedPage(
                page_number=number,
                content=text,
                section_label=str(getattr(sheet, "title", ""))[:200],
            )
    finally:
        if workbook is not None:
            workbook.close()

        handle.close()


def _join_capped(lines: Iterator[str]) -> str:
    """行を連結する。1 ページの上限を超えたら打ち切る。

    打ち切りを明示することで、「途中までしか検索に出ない」原因を追える。
    """

    buffer: list[str] = []
    size = 0

    for line in lines:
        buffer.append(line)
        size += len(line)

        if size >= MAX_PAGE_CHARS:
            buffer.append(f"\n…（{MAX_PAGE_CHARS}文字を超えたため以降を省略）\n")
            break

    return "".join(buffer)


def _docx_lines(word_document) -> Iterator[str]:
    """Word の段落と表を行として取り出す。"""

    for paragraph in word_document.paragraphs:
        text = (paragraph.text or "").strip()

        if text:
            yield text + "\n"

    for table in word_document.tables:
        for row in table.rows:
            cells = [(cell.text or "").strip() for cell in row.cells]
            joined = "\t".join(value for value in cells if value)

            if joined:
                yield joined + "\n"


def _extract_word(document: Document) -> Iterator[ExtractedPage]:
    """Word（.docx）。

    docx は物理ページの概念を持たない（ページ割りは描画時に決まる）ため、
    文字数で疑似ページへ切る。引用時に「およそどのあたりか」を示すのが目的。
    """

    docx = _require("docx", "python-docx")
    handle = _open_binary(document)

    try:
        try:
            word_document = docx.Document(handle)
        except Exception as exc:  # noqa: BLE001 - 破損・非対応形式
            raise ExtractionError(f"Word を読み込めません（破損の可能性）: {exc}") from exc

        yield from _paginate(_docx_lines(word_document))
    finally:
        handle.close()


def _slide_lines(slide) -> Iterator[str]:
    for shape in slide.shapes:
        if not getattr(shape, "has_text_frame", False):
            continue

        text = (shape.text_frame.text or "").strip()

        if text:
            yield text + "\n"


def _extract_powerpoint(document: Document) -> Iterator[ExtractedPage]:
    """PowerPoint（.pptx）。スライド 1 枚を 1 ページとして扱う。"""

    pptx = _require("pptx", "python-pptx")
    handle = _open_binary(document)

    try:
        try:
            presentation = pptx.Presentation(handle)
        except Exception as exc:  # noqa: BLE001 - 破損の可能性
            raise ExtractionError(
                f"PowerPoint を読み込めません（破損の可能性）: {exc}"
            ) from exc

        for number, slide in enumerate(presentation.slides, start=1):
            try:
                lines = list(_slide_lines(slide))
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError(f"スライド {number} を抽出できません: {exc}") from exc

            label = lines[0].strip()[:200] if lines else ""

            yield ExtractedPage(
                page_number=number,
                content="".join(lines),
                section_label=label,
            )
    finally:
        handle.close()


def _unsupported_legacy(document: Document) -> Iterator[ExtractedPage]:
    """旧バイナリ形式（.doc / .xls）。

    無償ライブラリでは本文抽出の品質が安定しない。黙って空を返すより、
    「変換してほしい」と明示して失敗させる方が運用が回る。
    """

    raise ExtractionError(
        "旧形式（.doc / .xls）の本文抽出は未対応です。"
        ".docx / .xlsx へ変換してから登録してください。"
    )


#: 形式 → 抽出関数。分岐を 1 か所に閉じ込め、対応形式を一覧で読めるようにする。
EXTRACTORS: dict[str, Callable[[Document], Iterator[ExtractedPage]]] = {
    FileType.PDF: _extract_pdf,
    FileType.XLSX: _extract_excel,
    FileType.XLSM: _extract_excel,
    FileType.XLS: _unsupported_legacy,
    FileType.DOCX: _extract_word,
    FileType.DOC: _unsupported_legacy,
    FileType.PPTX: _extract_powerpoint,
    FileType.TXT: _extract_plain_text,
    FileType.MD: _extract_plain_text,
}


def iter_pages(document: Document) -> Iterator[ExtractedPage]:
    """文書からページを 1 件ずつ取り出す（遅延評価）。

    大きなファイルを扱う経路はすべてこれを使う。`extract()` は結果を一覧で
    見たいとき（管理コマンド・テスト）のための薄い包み。
    """

    extractor = EXTRACTORS.get(document.file_type)

    if extractor is None:
        raise ExtractionError(f"未対応の形式です: {document.file_type or '不明'}")

    for page in extractor(document):
        yield ExtractedPage(
            page_number=page.page_number,
            content=(page.content or "").strip(),
            section_label=(page.section_label or "")[:200],
        )


def extract(document: Document) -> list[ExtractedPage]:
    """文書の本文を抽出して返す（公開 API）。

    全ページをメモリへ載せるため、巨大ファイルを扱う処理は `iter_pages()` か
    `ingest()` を使うこと。
    """

    return list(iter_pages(document))


@transaction.atomic
def _replace_pages(document: Document) -> tuple[int, int, int]:
    """既存ページを作り直す。戻り値は (保存ページ数, 文字数, 抽出元ページ数)。

    差分更新にしないのは、抽出ロジックを変えたときに古いページが残って
    引用位置がずれる事故を防ぐため。
    """

    DocumentPage.objects.filter(document=document).delete()

    batch: list[DocumentPage] = []
    saved = 0
    characters = 0
    source_pages = 0

    for page in iter_pages(document):
        source_pages += 1

        # 空ページ（表紙・区切り）は保存しない。空チャンクは検索の邪魔にしかならない。
        if not page.content:
            continue

        batch.append(
            DocumentPage(
                document=document,
                page_number=page.page_number,
                section_label=page.section_label,
                content=page.content,
            )
        )
        saved += 1
        characters += len(page.content)

        if len(batch) >= PAGE_BATCH_SIZE:
            DocumentPage.objects.bulk_create(batch)
            batch = []

    if batch:
        DocumentPage.objects.bulk_create(batch)

    return saved, characters, source_pages


def _finish(job: IngestJob, *, status: str, message: str = "", stats: dict | None = None) -> IngestJob:
    job.status = status
    job.message = message[:MESSAGE_MAX_LENGTH]
    job.stats = stats or {}
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "message", "stats", "finished_at", "updated_at"])

    return job


def _mark_document(document: Document, status: str) -> None:
    """文書の状態を更新する。

    抽出に失敗した文書を `error` にすると、`rebuild_index()` の対象から外れる。
    「空のまま検索対象に入っている」状態を作らないための連動。
    """

    if document.status == status:
        return

    document.status = status
    document.save(update_fields=["status", "updated_at"])


def ingest(document: Document, *, build_index: bool = True) -> ExtractionResult:
    """本文抽出 → ページ保存 → インデックス構築までを実行する。

    例外はここで閉じ、すべて `IngestJob` の失敗として記録する。画面から呼んでも
    500 にならないことがこの関数の契約。
    """

    job = IngestJob.objects.create(
        tenant=document.tenant,
        document=document,
        job_type=IngestJob.JobType.CONVERT,
        status=IngestJob.Status.RUNNING,
        started_at=timezone.now(),
    )

    try:
        saved, characters, source_pages = _replace_pages(document)
    except ExtractionError as exc:
        _mark_document(document, DocumentStatus.ERROR)

        return ExtractionResult(
            document=document,
            job=_finish(job, status=IngestJob.Status.FAILED, message=str(exc)),
        )
    except Exception as exc:  # noqa: BLE001 - ライブラリ由来の想定外例外も画面へ漏らさない
        _mark_document(document, DocumentStatus.ERROR)

        return ExtractionResult(
            document=document,
            job=_finish(
                job,
                status=IngestJob.Status.FAILED,
                message=f"想定外のエラーで抽出できませんでした: {exc}",
            ),
        )

    stats = {
        "file_type": document.file_type,
        "pages": saved,
        "source_pages": source_pages,
        "characters": characters,
    }

    if characters == 0:
        # 画像だけの PDF・空シートのみの Excel。成功にすると「検索に出ない」原因が消える。
        DocumentPage.objects.filter(document=document).delete()
        _mark_document(document, DocumentStatus.ERROR)

        return ExtractionResult(
            document=document,
            job=_finish(
                job,
                status=IngestJob.Status.FAILED,
                message=(
                    f"本文を1文字も抽出できませんでした（抽出元 {source_pages} ページ）。"
                    "画像のみの PDF などで、OCR が必要な可能性があります。"
                ),
                stats=stats,
            ),
            source_page_count=source_pages,
        )

    _mark_document(document, DocumentStatus.ACTIVE)
    _finish(job, status=IngestJob.Status.SUCCEEDED, stats=stats)

    index_job = _build_index(document) if build_index else None

    return ExtractionResult(
        document=document,
        job=job,
        page_count=saved,
        char_count=characters,
        source_page_count=source_pages,
        index_job=index_job,
    )


def _build_index(document: Document) -> IngestJob:
    """抽出済み本文をチャンク化してインデックスへ載せる。

    `apps.rag` の import を関数内に置くのは、documents → rag の依存を
    実行時だけに留め、モジュール読み込み順の事故を避けるため。
    """

    from apps.rag.models import IndexScope, VectorIndex
    from apps.rag.services.indexer import rebuild_index

    job = IngestJob.objects.create(
        tenant=document.tenant,
        document=document,
        job_type=IngestJob.JobType.INDEX,
        status=IngestJob.Status.RUNNING,
        started_at=timezone.now(),
    )

    try:
        index, _ = VectorIndex.objects.get_or_create(
            tenant=document.tenant,
            project=document.project,
            defaults={
                "scope": IndexScope.PROJECT if document.project_id else IndexScope.TENANT
            },
        )
        result = rebuild_index(index)
    except Exception as exc:  # noqa: BLE001 - Embedding 側の失敗で抽出結果を捨てない
        return _finish(
            job,
            status=IngestJob.Status.FAILED,
            message=f"インデックス構築に失敗しました: {exc}",
        )

    return _finish(
        job,
        status=IngestJob.Status.SUCCEEDED,
        stats={"chunks": result.chunk_count, "documents": result.document_count},
    )
