from django.contrib import admin

from apps.rag.models import (
    AnswerCitation,
    ChatMessage,
    ChatSession,
    Chunk,
    EvaluationCase,
    EvaluationRun,
    GoldenQuestion,
    RagAnswer,
    RetrievalQuery,
    RetrievedChunk,
    VectorIndex,
)


@admin.register(GoldenQuestion)
class GoldenQuestionAdmin(admin.ModelAdmin):
    list_display = ("question", "tenant", "category", "must_abstain", "is_active")
    list_filter = ("tenant", "category", "is_active", "must_abstain")
    search_fields = ("question", "category")
    filter_horizontal = ("expected_documents",)


@admin.register(EvaluationRun)
class EvaluationRunAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tenant", "suite", "case_count", "evaluable", "recall_at_k", "mrr")
    list_filter = ("tenant", "suite", "evaluable")


@admin.register(EvaluationCase)
class EvaluationCaseAdmin(admin.ModelAdmin):
    list_display = ("question", "run", "position", "passed", "first_hit_rank", "recall")
    list_filter = ("passed", "evaluable")


@admin.register(VectorIndex)
class VectorIndexAdmin(admin.ModelAdmin):
    list_display = ("__str__", "scope", "status", "embedding_model", "chunk_count", "built_at")
    list_filter = ("scope", "status", "tenant")


@admin.register(Chunk)
class ChunkAdmin(admin.ModelAdmin):
    list_display = ("chunk_key", "document", "page_number", "position", "token_count")
    list_filter = ("index", "document")
    search_fields = ("chunk_key", "text")


@admin.register(RetrievalQuery)
class RetrievalQueryAdmin(admin.ModelAdmin):
    list_display = ("question", "tenant", "project", "user", "top_k", "elapsed_ms", "created_at")
    list_filter = ("tenant", "used_rerank")
    search_fields = ("question",)


admin.site.register([AnswerCitation, ChatMessage, ChatSession, RagAnswer, RetrievedChunk])
