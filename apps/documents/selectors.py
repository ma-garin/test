"""文書・ひな型の参照クエリ。

テナント分離をビューごとに書くと必ずどこかで漏れるため、参照系はここへ集約する
（`apps.projects.selectors` と同じ方針）。
"""

from __future__ import annotations

from django.db.models import QuerySet

from apps.documents.models import Document, Template


def documents_for(user, tenant) -> QuerySet[Document]:
    """ユーザーが参照できる文書。

    スーパーユーザーがテナント未選択のときだけ全件を返す。運用者が横断確認する
    ケースがあるためで、一般ユーザーには絶対に自テナント以外を見せない。
    """

    queryset = Document.objects.alive().select_related("project", "uploaded_by")

    if user is None or not user.is_authenticated:
        return queryset.none()

    if user.is_superuser and tenant is None:
        return queryset

    return queryset.filter(tenant=tenant or user.tenant)


def templates_for(user, tenant) -> QuerySet[Template]:
    """ユーザーが参照できるひな型。

    ひな型は RAG 対象に含めないため `Document` とは別系統だが、テナント分離の
    条件は揃えておく。
    """

    queryset = Template.objects.alive()

    if user is None or not user.is_authenticated:
        return queryset.none()

    if user.is_superuser and tenant is None:
        return queryset

    return queryset.filter(tenant=tenant or user.tenant)
