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


class ChunkSourceType(models.TextChoices):
    """チャンクの出典。

    文書だけでなく業務データ（課題・不具合など）も同じインデックスへ載せるため、
    検索結果で出典を区別できるように種別を持つ。「不具合 #a1b2c3d4」と分かることが
    過去障害事例検索の前提になる。
    """

    DOCUMENT = "document", "文書"
    ISSUE = "issue", "課題"
    DEFECT = "defect", "不具合"
    RISK = "risk", "リスク"
    CHANGE_REQUEST = "change_request", "変更要求"
    WBS_TASK = "wbs_task", "WBSタスク"


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
        null=True,
        blank=True,
        help_text="業務データ由来のチャンクは文書に紐づかないため null になる。",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="rag_chunks",
        null=True,
        blank=True,
        help_text="案件別に検索範囲を切るための境界。テナント共通の文書は null。",
    )
    source_type = models.CharField(
        "出典種別",
        max_length=32,
        choices=ChunkSourceType.choices,
        default=ChunkSourceType.DOCUMENT,
        db_index=True,
    )
    source_id = models.UUIDField("出典レコードID", null=True, blank=True, db_index=True)
    source_label = models.CharField("出典表示名", max_length=200, blank=True)
    source_updated_at = models.DateTimeField(
        "出典の更新日時",
        null=True,
        blank=True,
        help_text="差分インデックスの判定に使う。元レコードの updated_at を写す。",
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

    @property
    def is_business(self) -> bool:
        """業務データ由来か。画面で出典バッジを出し分けるために使う。"""

        return self.source_type != ChunkSourceType.DOCUMENT

    @property
    def source_title(self) -> str:
        """検索結果に出す出典名。文書名と業務データ名を同じ口で扱う。"""

        if self.source_label:
            return self.source_label

        return self.document.title if self.document_id else "（出典不明）"

    def __str__(self) -> str:
        return f"{self.chunk_key} ({self.source_title})"


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


class GoldenQuestion(TimeStampedModel):
    """評価用の「質問と期待する根拠」の組（Golden Dataset）。

    旧実装では `eval/golden_dataset.json` に置いていたが、期待文書の実在性を
    検証できないという欠点があった。ここでは FK として持ち、文書が削除された
    ことを検知できるようにする（黙って除外しない、が要件）。

    `expected_document_titles` は登録時のスナップショット。M2M は参照先を
    物理削除されると行ごと消えるため、それだけでは「消えたこと」を検知できない。
    """

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="golden_questions",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="golden_questions",
    )
    question = models.TextField("質問")
    category = models.CharField("カテゴリ", max_length=60, blank=True)
    expected_documents = models.ManyToManyField(
        "documents.Document",
        verbose_name="期待する文書",
        blank=True,
        related_name="golden_questions",
    )
    expected_document_titles = models.JSONField(
        "期待文書名スナップショット",
        default=list,
        blank=True,
        help_text="登録時点の文書名。参照が消えたことを検知するために保持する。",
    )
    expected_terms = models.JSONField("期待キーワード", default=list, blank=True)
    required_sections = models.JSONField(
        "回答に必須のセクション",
        default=list,
        blank=True,
        help_text="回答評価 dry-run で本文に含まれることを確認する見出し。",
    )
    must_abstain = models.BooleanField(
        "根拠不足なら回答を抑制すべき",
        default=False,
        help_text="登録文書に無いはずの質問。断定せず確認を促すことを期待する。",
    )
    is_active = models.BooleanField("有効", default=True)
    note = models.TextField("備考", blank=True)

    class Meta:
        verbose_name = "Golden質問"
        verbose_name_plural = "Golden質問"
        ordering = ["category", "created_at"]
        indexes = [models.Index(fields=["tenant", "is_active"])]

    def __str__(self) -> str:
        return self.question[:60]

    def sync_expected_snapshot(self) -> None:
        """期待文書名のスナップショットを現在の M2M から作り直す。"""

        titles = list(self.expected_documents.values_list("title", flat=True))
        self.expected_document_titles = titles
        self.save(update_fields=["expected_document_titles", "updated_at"])


class EvaluationSuite(models.TextChoices):
    """評価スイート。何を測るかで分ける。"""

    RETRIEVAL = "retrieval", "検索評価（ベクトル+語彙）"
    RETRIEVAL_OFFLINE = "retrieval_offline", "APIなし検索評価（語彙のみ）"
    ANSWER = "answer", "回答評価 dry-run"
    STATIC = "static", "静的チェック"


class EvaluationRun(TimeStampedModel):
    """評価の 1 回分。履歴として残し、前回との差分を出せるようにする。

    指標を null 可にしているのは「0点」と「評価不能」を区別するため。
    Golden が 0 件のときに Recall 100% と出すのは、最も危険な嘘になる。
    """

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="evaluation_runs",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evaluation_runs",
    )
    index = models.ForeignKey(
        VectorIndex,
        verbose_name="対象インデックス",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evaluation_runs",
    )
    executed_by = models.ForeignKey(
        "accounts.User",
        verbose_name="実行者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evaluation_runs",
    )
    suite = models.CharField("スイート", max_length=32, choices=EvaluationSuite.choices)
    top_k = models.PositiveSmallIntegerField("評価対象の上位件数", default=8)
    case_count = models.PositiveIntegerField("評価件数", default=0)
    evaluable = models.BooleanField("評価可能", default=False)
    unavailable_reason = models.CharField("評価不能の理由", max_length=200, blank=True)
    recall_at_k = models.FloatField("Recall@K", null=True, blank=True)
    precision_at_k = models.FloatField("Precision@K", null=True, blank=True)
    mrr = models.FloatField("MRR", null=True, blank=True)
    pass_rate = models.FloatField("合格率", null=True, blank=True)
    issues = models.JSONField("検出事項", default=list, blank=True)
    metrics = models.JSONField("補助指標", default=dict, blank=True)

    class Meta:
        verbose_name = "評価実行"
        verbose_name_plural = "評価実行"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "suite", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.get_suite_display()} {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def issue_count(self) -> int:
        return len(self.issues or [])


class EvaluationCase(TimeStampedModel):
    """評価 1 問分の結果。どの質問がなぜ落ちたかを追えるようにする。"""

    run = models.ForeignKey(
        EvaluationRun,
        verbose_name="評価実行",
        on_delete=models.CASCADE,
        related_name="cases",
    )
    golden = models.ForeignKey(
        GoldenQuestion,
        verbose_name="Golden質問",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cases",
    )
    position = models.PositiveSmallIntegerField("表示順", default=0)
    question = models.TextField("質問")
    evaluable = models.BooleanField("評価可能", default=True)
    passed = models.BooleanField("合格", default=False)
    first_hit_rank = models.PositiveSmallIntegerField("初出順位", null=True, blank=True)
    recall = models.FloatField("Recall", null=True, blank=True)
    precision = models.FloatField("Precision", null=True, blank=True)
    reciprocal_rank = models.FloatField("逆順位", null=True, blank=True)
    matched_documents = models.JSONField("命中した期待文書", default=list, blank=True)
    missing_documents = models.JSONField("出なかった期待文書", default=list, blank=True)
    issues = models.JSONField("検出事項", default=list, blank=True)
    detail = models.JSONField("詳細", default=dict, blank=True)

    class Meta:
        verbose_name = "評価ケース"
        verbose_name_plural = "評価ケース"
        ordering = ["run", "position"]

    def __str__(self) -> str:
        return self.question[:40]


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
