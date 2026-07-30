"""文書台帳と取込ジョブ、Excel ひな型。

旧実装の `index_map.json`（文書台帳）と `template_registry.json`（ひな型台帳）を
テーブル化したもの。旧台帳が抱えていた「本番パスと検証パスの混在」を避けるため、
ファイル実体は Django のストレージ経由で保持し、絶対パスは持たない。
"""

from __future__ import annotations

from django.db import models

from apps.core.models import SoftDeleteModel, TimeStampedModel


class DocumentStatus(models.TextChoices):
    """旧 index_map.json の status をそのまま踏襲する。"""

    ACTIVE = "active", "RAG対象"
    EXCLUDED = "excluded", "RAG対象外"
    MISSING = "missing", "ファイル未検出"
    ERROR = "error", "変換・登録エラー"


class FileType(models.TextChoices):
    PDF = "pdf", "PDF"
    XLSX = "xlsx", "Excel (.xlsx)"
    XLSM = "xlsm", "Excel (.xlsm)"
    XLS = "xls", "Excel (.xls)"
    DOCX = "docx", "Word (.docx)"
    DOC = "doc", "Word (.doc)"
    PPTX = "pptx", "PowerPoint (.pptx)"


def document_upload_path(instance: Document, filename: str) -> str:
    scope = instance.project.code if instance.project_id else "_global"

    return f"documents/{instance.tenant.code}/{scope}/{filename}"


class Document(SoftDeleteModel):
    """RAG 対象の原本文書。

    `project` が null なら、テナント共通のナレッジ（社内標準プロセス等）として扱う。
    """

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="documents",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="documents",
        null=True,
        blank=True,
        help_text="未設定ならテナント共通ナレッジ。",
    )
    title = models.CharField("文書名", max_length=300)
    file = models.FileField("ファイル", upload_to=document_upload_path)
    file_type = models.CharField("種別", max_length=16, choices=FileType.choices)
    file_size = models.BigIntegerField("サイズ", default=0)
    sha256 = models.CharField("ハッシュ", max_length=64, blank=True, db_index=True)
    status = models.CharField(
        "状態",
        max_length=16,
        choices=DocumentStatus.choices,
        default=DocumentStatus.ACTIVE,
    )
    source_note = models.CharField("出典メモ", max_length=300, blank=True)
    uploaded_by = models.ForeignKey(
        "accounts.User",
        verbose_name="登録者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_documents",
    )
    last_indexed_at = models.DateTimeField("最終インデックス日時", null=True, blank=True)

    class Meta:
        verbose_name = "文書"
        verbose_name_plural = "文書"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "status"])]

    def __str__(self) -> str:
        return self.title

    @property
    def needs_reindex(self) -> bool:
        """本文が更新されているのにインデックスが古い状態か。"""

        if self.status != DocumentStatus.ACTIVE:
            return False

        return self.last_indexed_at is None or self.last_indexed_at < self.updated_at


class DocumentPage(TimeStampedModel):
    """変換後の中間表現。

    旧 `03.json/RES_*.json` の 1 item に相当する。チャンク分割前の単位で残しておくと、
    チャンク戦略を変えても再変換せずに作り直せる。
    """

    document = models.ForeignKey(
        Document,
        verbose_name="文書",
        on_delete=models.CASCADE,
        related_name="pages",
    )
    page_number = models.PositiveIntegerField("ページ/シート番号", default=1)
    section_label = models.CharField("シート名・見出し", max_length=200, blank=True)
    content = models.TextField("本文")

    class Meta:
        verbose_name = "文書ページ"
        verbose_name_plural = "文書ページ"
        ordering = ["document", "page_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "page_number", "section_label"],
                name="uniq_document_page",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document.title} p.{self.page_number}"


class IngestJob(TimeStampedModel):
    """取込・インデックス構築の実行履歴。

    旧実装では Streamlit の画面内で同期実行していた。Django 版では非同期ワーカーへ
    出せるよう、状態を持つジョブとして記録する。
    """

    class JobType(models.TextChoices):
        CONVERT = "convert", "本文変換"
        INDEX = "index", "インデックス構築"
        REINDEX = "reindex", "再インデックス"

    class Status(models.TextChoices):
        QUEUED = "queued", "待機中"
        RUNNING = "running", "実行中"
        SUCCEEDED = "succeeded", "成功"
        FAILED = "failed", "失敗"

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="ingest_jobs",
    )
    document = models.ForeignKey(
        Document,
        verbose_name="文書",
        on_delete=models.CASCADE,
        related_name="ingest_jobs",
        null=True,
        blank=True,
    )
    job_type = models.CharField("種別", max_length=16, choices=JobType.choices)
    status = models.CharField("状態", max_length=16, choices=Status.choices, default=Status.QUEUED)
    started_at = models.DateTimeField("開始日時", null=True, blank=True)
    finished_at = models.DateTimeField("終了日時", null=True, blank=True)
    message = models.TextField("メッセージ", blank=True)
    stats = models.JSONField("統計", default=dict, blank=True)

    class Meta:
        verbose_name = "取込ジョブ"
        verbose_name_plural = "取込ジョブ"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_job_type_display()} / {self.get_status_display()}"


class Template(SoftDeleteModel):
    """Excel ひな型。

    ひな型は RAG 対象に含めない。回答の出力先としてのみ使う、という旧実装の分離方針を
    そのまま維持する。
    """

    class MappingStatus(models.TextChoices):
        UNCONFIGURED = "unconfigured", "未設定"
        DRAFT = "draft", "下書き"
        APPROVED = "approved", "承認済み"
        NEEDS_REVIEW = "needs_review", "要確認"

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="templates",
    )
    name = models.CharField("ひな型名", max_length=200)
    description = models.TextField("説明", blank=True)
    file = models.FileField("ひな型ファイル", upload_to="templates/")
    keywords = models.CharField("検索キーワード", max_length=300, blank=True)
    sheet_outline = models.JSONField("シート構成", default=list, blank=True)
    field_mapping = models.JSONField(
        "項目マッピング",
        default=dict,
        blank=True,
        help_text="回答項目 → セル位置の対応。AI 提案後、人が確認して承認する。",
    )
    mapping_status = models.CharField(
        "マッピング状態",
        max_length=16,
        choices=MappingStatus.choices,
        default=MappingStatus.UNCONFIGURED,
    )

    class Meta:
        verbose_name = "ひな型"
        verbose_name_plural = "ひな型"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class TemplateOutput(TimeStampedModel):
    """ひな型へ回答を書き出した成果物ファイル。"""

    template = models.ForeignKey(
        Template,
        verbose_name="ひな型",
        on_delete=models.CASCADE,
        related_name="outputs",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="template_outputs",
    )
    file = models.FileField("出力ファイル", upload_to="template_outputs/")
    generated_by = models.ForeignKey(
        "accounts.User",
        verbose_name="生成者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="template_outputs",
    )
    source_answer = models.ForeignKey(
        "rag.RagAnswer",
        verbose_name="元になった回答",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="template_outputs",
    )
    warnings = models.JSONField("警告", default=list, blank=True)

    class Meta:
        verbose_name = "ひな型出力"
        verbose_name_plural = "ひな型出力"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.template.name} / {self.created_at:%Y-%m-%d %H:%M}"
