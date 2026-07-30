from django.contrib import admin

from apps.rag.models import (
    AnswerCitation,
    ChatMessage,
    ChatSession,
    Chunk,
    RagAnswer,
    RetrievalQuery,
    RetrievedChunk,
    VectorIndex,
)


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
