"""類似案件検索。

「似た案件で何が起きたか」を引けるようにする。案件のプロフィール
（規模・進行フェーズ・課題やリスクの傾向・不具合の検出工程）をテキスト化し、
既存の語彙検索（TF-IDF）で比較する。

Embedding API も LLM も使わない（ADR-0003）。`LexicalIndex` を再利用するのは、
案件プロフィールが「短い日本語テキストの集合」で、チャンク検索と性質が同じため。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.projects.models import Project
from apps.projects.selectors import projects_for
from apps.rag.services.lexical import LexicalIndex

#: 案件詳細に出す類似案件の件数。
DEFAULT_TOP_K = 5

#: プロフィールへ含める各明細の上限。多いと語彙が薄まり、どの案件とも似てしまう。
TASK_SAMPLE = 20
DETAIL_SAMPLE = 10

#: 根拠として画面に出す共通キーワードの数。
REASON_TERM_LIMIT = 5

PROFILE_PREFETCH = ("wbstask_set", "issue_set", "risk_set", "defect_set")


@dataclass(frozen=True)
class SimilarProject:
    project: Project
    score: float
    shared_terms: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        """なぜ似ていると判定したか。根拠を出さない推薦は使われない。"""

        if not self.shared_terms:
            return "案件プロフィール全体が近い"

        return "共通キーワード: " + "、".join(self.shared_terms[:REASON_TERM_LIMIT])


def _size_label(task_count: int) -> str:
    if task_count >= 100:
        return "大規模案件"

    if task_count >= 30:
        return "中規模案件"

    return "小規模案件"


def _phase_label(progress_percent) -> str:
    value = float(progress_percent or 0)

    if value >= 90:
        return "終盤フェーズ"

    if value >= 50:
        return "後半フェーズ"

    if value >= 20:
        return "中盤フェーズ"

    return "序盤フェーズ"


def profile_text(project: Project) -> str:
    """案件を 1 本のテキストにする。

    数値をそのまま入れても語彙一致しないため、規模・フェーズはラベル化する。
    """

    tasks = list(project.wbstask_set.all())
    issues = list(project.issue_set.all())
    risks = list(project.risk_set.all())
    defects = list(project.defect_set.all())

    parts: list[str] = [
        project.name,
        project.description,
        project.get_status_display(),
        project.get_rag_status_display(),
        _size_label(len(tasks)),
        _phase_label(project.progress_percent),
    ]
    parts += [task.name for task in tasks[:TASK_SAMPLE]]
    parts += [issue.title for issue in issues[:DETAIL_SAMPLE]]
    parts += [issue.get_severity_display() for issue in issues[:DETAIL_SAMPLE]]
    parts += [risk.title for risk in risks[:DETAIL_SAMPLE]]
    parts += [risk.mitigation for risk in risks[:DETAIL_SAMPLE]]
    parts += [defect.title for defect in defects[:DETAIL_SAMPLE]]
    parts += [defect.phase for defect in defects[:DETAIL_SAMPLE]]

    return "\n".join(part for part in parts if part)


def _candidates(candidates, exclude_pk) -> list[Project]:
    if hasattr(candidates, "prefetch_related"):
        candidates = candidates.prefetch_related(*PROFILE_PREFETCH)

    return [project for project in candidates if project.pk != exclude_pk]


def similar_projects(
    project: Project,
    candidates,
    *,
    top_k: int = DEFAULT_TOP_K,
) -> list[SimilarProject]:
    """`candidates` の中から `project` に似た案件を類似度順に返す。

    候補の絞り込み（テナント・権限）は呼び出し側の責務。ここでは順位付けだけを行う。
    """

    others = _candidates(candidates, project.pk)

    if not others:
        return []

    by_id = {str(item.pk): item for item in others}
    index = LexicalIndex.build((str(item.pk), profile_text(item)) for item in others)
    hits = index.search(profile_text(project), top_k=top_k)

    return [
        SimilarProject(
            project=by_id[hit.chunk_id],
            score=hit.score,
            shared_terms=list(hit.matched_terms),
        )
        for hit in hits
        if hit.chunk_id in by_id
    ]


def similar_projects_for(request, project: Project, *, top_k: int = DEFAULT_TOP_K):
    """画面から使う入口。候補は必ず参照権限のある案件に限定する。"""

    return similar_projects(
        project,
        projects_for(getattr(request, "user", None), getattr(request, "tenant", None)),
        top_k=top_k,
    )
