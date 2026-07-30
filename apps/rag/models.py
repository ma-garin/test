"""RAG のチャンク・検索・回答・チャット。

旧実装では `04.faiss_index/chunks.json` と `07.feedback/*.jsonl` に分散していた。
検索結果と回答、そして引用根拠の関係を追跡できるよう、テーブルとして正規化する。

ベクトル本体は RDB に置かない。`VectorIndex` がベクトルストア（FAISS 等）の
所在とメタ情報だけを持ち、実体は `apps.rag.services.vector_store` が扱う。
"""

from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class IndexScope(models.TextChoices):
    TENANT = "tenant", "テナント共通"
    PROJECT = "project", "案件別"


class VectorIndex(TimeStampedModel):
    """検索インデックスの単位。テナント／案件の参照分離境界でもある。"""

    class Status(models.TextChoices):
        PENDING = "pending", "未構築"
        BUILDING = "building", "構築中"
        READY = "ready", "構築済み"
        ERROR = "error", "要確認"

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="vector_indexes",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="vector_indexes",
        null=True,
        blank=True,
    )
    scope = models.CharField("範囲", max_length=16, choices=IndexScope.choices, default=IndexScope.TENANT)
    status = models.CharField("状態", max_length=16, choices=Status.choices, default=Status.PENDING)
    embedding_provider = models.CharField("Embeddingプロバイダ", max_length=32, default="local_hash")
    embedding_model = models.CharField("Embeddingモデル", max_length=120, default="local-hash-v1")
    dimension = models.PositiveIntegerField("次元数", default=0)
    chunk_count = models.PositiveIntegerField("チャンク数", default=0)
    built_at = models.DateTimeField("構築日時", null=True, blank=True)

    class Meta:
        verbose_name = "検索インデックス"
        verbose_name_plural = "検索インデックス"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "project"],
                name="uniq_vector_index_per_scope",
            ),
        ]

    def __str__(self) -> str:
        target = self.project.code if self.project_id else "共通"

        return f"{self.tenant.code} / {target}"

    @property
    def is_stale(self) -> bool:
        """Embedding 設定が現在の設定と食い違っていないか。

        設定を変えたまま再構築せずに検索すると、ベクトル空間が混ざって精度が落ちる。
        """

        from django.conf import settings

        if self.embedding_provider != settings.AI_PROVIDER:
            return True

        return self.dimension == 0


class Chunk(TimeStampedModel):
    """検索対象の最小単位。"""

    index = models.ForeignKey(
        VectorIndex,
        verbose_name="インデックス",
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    document = models.ForeignKey(
        "documents.Document",
        verbose_name="文書",
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    chunk_key = models.CharField(
        "チャンクキー",
        max_length=64,
        db_index=True,
        help_text="文書・ページ・位置から決まる安定 ID。再構築しても同じ値になる。",
    )
    page_number = models.PositiveIntegerField("ページ", default=1)
    position = models.PositiveIntegerField("文書内の順序", default=0)
    text = models.TextField("本文")
    token_count = models.PositiveIntegerField("トークン数", default=0)
    metadata = models.JSONField("メタデータ", default=dict, blank=True)

    class Meta:
        verbose_name = "チャンク"
        verbose_name_plural = "チャンク"
        ordering = ["document", "position"]
        constraints = [
            models.UniqueConstraint(fields=["index", "chunk_key"], name="uniq_chunk_key_per_index"),
        ]

    def __str__(self) -> str:
        return f"{self.chunk_key} ({self.document.title})"


class RetrievalQuery(TimeStampedModel):
    """1 回の検索実行。ハイブリッド検索の内訳を残す。"""

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="retrieval_queries",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retrieval_queries",
    )
    user = models.ForeignKey(
        "accounts.User",
        verbose_name="実行者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retrieval_queries",
    )
    question = models.TextField("質問")
    expanded_queries = models.JSONField("拡張クエリ", default=list, blank=True)
    top_k = models.PositiveSmallIntegerField("取得件数", default=8)
    used_vector = models.BooleanField("ベクトル検索", default=True)
    used_lexical = models.BooleanField("語彙検索", default=True)
    used_rerank = models.BooleanField("LLMリランク", default=False)
    elapsed_ms = models.PositiveIntegerField("所要時間(ms)", default=0)

    class Meta:
        verbose_name = "検索実行"
        verbose_name_plural = "検索実行"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.question[:60]


class RetrievedChunk(TimeStampedModel):
    """検索結果 1 件。各スコアを分けて持ち、順位の根拠を説明できるようにする。"""

    query = models.ForeignKey(
        RetrievalQuery,
        verbose_name="検索実行",
        on_delete=models.CASCADE,
        related_name="results",
    )
    chunk = models.ForeignKey(
        Chunk,
        verbose_name="チャンク",
        on_delete=models.CASCADE,
        related_name="retrievals",
    )
    rank = models.PositiveSmallIntegerField("順位")
    vector_score = models.FloatField("ベクトルスコア", null=True, blank=True)
    lexical_score = models.FloatField("語彙スコア", null=True, blank=True)
    rerank_score = models.FloatField("リランクスコア", null=True, blank=True)
    final_score = models.FloatField("最終スコア", default=0.0)

    class Meta:
        verbose_name = "検索結果"
        verbose_name_plural = "検索結果"
        ordering = ["query", "rank"]
        constraints = [
            models.UniqueConstraint(fields=["query", "rank"], name="uniq_rank_per_query"),
        ]

    def __str__(self) -> str:
        return f"#{self.rank} {self.chunk.chunk_key}"


class RagAnswer(TimeStampedModel):
    """生成された回答。根拠・推測・一般情報を分離して保持する（REQ-AG-007）。"""

    query = models.OneToOneField(
        RetrievalQuery,
        verbose_name="検索実行",
        on_delete=models.CASCADE,
        related_name="answer",
    )
    body = models.TextField("回答本文")
    summary = models.TextField("判断サマリ", blank=True)
    grounded_findings = models.TextField("登録情報から確認できること", blank=True)
    general_guidance = models.TextField("一般情報による補足", blank=True)
    unverified_points = models.TextField("資料上は確認できないこと", blank=True)
    recommended_actions = models.JSONField("推奨アクション", default=list, blank=True)
    follow_up_questions = models.JSONField("追加確認事項", default=list, blank=True)
    provider = models.CharField("生成プロバイダ", max_length=32, blank=True)
    model = models.CharField("生成モデル", max_length=120, blank=True)
    knowledge_balance = models.PositiveSmallIntegerField(
        "RAG/一般情報バランス",
        default=70,
        help_text="0 に近いほど一般知識寄り、100 に近いほど登録文書寄り。",
    )

    class Meta:
        verbose_name = "RAG回答"
        verbose_name_plural = "RAG回答"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.body[:60]


class AnswerCitation(TimeStampedModel):
    """回答本文のどの主張が、どのチャンクを根拠にしているか（REQ-AG-006）。"""

    answer = models.ForeignKey(
        RagAnswer,
        verbose_name="回答",
        on_delete=models.CASCADE,
        related_name="citations",
    )
    chunk = models.ForeignKey(
        Chunk,
        verbose_name="根拠チャンク",
        on_delete=models.CASCADE,
        related_name="citations",
    )
    claim = models.TextField("対応する主張", blank=True)
    quoted_text = models.TextField("引用", blank=True)

    class Meta:
        verbose_name = "根拠"
        verbose_name_plural = "根拠"

    def __str__(self) -> str:
        return f"{self.chunk.chunk_key}"


class ChatSession(TimeStampedModel):
    """RAG チャットの 1 スレッド。短期メモリの単位。"""

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="chat_sessions",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_sessions",
    )
    user = models.ForeignKey(
        "accounts.User",
        verbose_name="利用者",
        on_delete=models.CASCADE,
        related_name="chat_sessions",
    )
    title = models.CharField("タイトル", max_length=200, blank=True)
    is_archived = models.BooleanField("アーカイブ", default=False)

    class Meta:
        verbose_name = "チャットセッション"
        verbose_name_plural = "チャットセッション"
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title or f"session {self.pk}"


class ChatMessage(TimeStampedModel):
    class Role(models.TextChoices):
        USER = "user", "利用者"
        ASSISTANT = "assistant", "AI"
        SYSTEM = "system", "システム"

    session = models.ForeignKey(
        ChatSession,
        verbose_name="セッション",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField("発言者", max_length=16, choices=Role.choices)
    content = models.TextField("本文")
    answer = models.ForeignKey(
        RagAnswer,
        verbose_name="対応する回答",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_messages",
    )

    class Meta:
        verbose_name = "チャットメッセージ"
        verbose_name_plural = "チャットメッセージ"
        ordering = ["session", "created_at"]

    def __str__(self) -> str:
        return f"{self.get_role_display()}: {self.content[:40]}"
