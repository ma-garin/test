"""評価の実行と履歴保存。

管理コマンドと画面の両方から同じ関数を呼ぶ。実行経路によって結果が変わると
評価そのものが信用できなくなるため、入口を 1 本にする。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import transaction

from apps.rag import selectors as rag_selectors
from apps.rag.models import EvaluationCase, EvaluationRun, EvaluationSuite
from apps.rag.services.evaluation import answer as answer_service
from apps.rag.services.evaluation import golden as golden_service
from apps.rag.services.evaluation import metrics as metrics_service
from apps.rag.services.evaluation import retrieval as retrieval_service
from apps.rag.services.evaluation import static_check as static_service

#: 画面に必ず出す指標の定義。何を測っているか分からない数字は使わせない。
METRIC_DEFINITIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "recall_at_k",
        "Recall@K",
        "%",
        "期待する文書のうち、上位K件の検索結果に現れたものの割合。質問ごとに求めて平均する。",
    ),
    (
        "mrr",
        "MRR",
        "",
        "期待文書が最初に現れた順位の逆数（1位なら1.0、2位なら0.5、出なければ0）の平均。",
    ),
    (
        "precision_at_k",
        "Precision@K",
        "%",
        "上位K件の検索結果のうち、期待文書に属するものの割合。結果がK件未満なら取得件数を分母にする。",
    ),
    (
        "pass_rate",
        "合格率",
        "%",
        "検出事項が1件も無かった質問の割合。期待文書の欠損があると合格にならない。",
    ),
)


@dataclass(frozen=True)
class MetricDelta:
    """前回実行との差分。劣化に気づくための表示単位。"""

    key: str
    label: str
    unit: str
    definition: str
    current: float | None
    previous: float | None
    delta: float | None

    @property
    def tone(self) -> str:
        """差分の色分け。g=改善 / r=劣化 / n=変化なし・比較不能。"""

        if self.delta is None or abs(self.delta) < 1e-9:
            return "n"

        return "g" if self.delta > 0 else "r"

    @property
    def has_previous(self) -> bool:
        return self.previous is not None


def _scaled(key: str, unit: str, value: float | None) -> float | None:
    if value is None:
        return None

    return round(value * 100, 1) if unit == "%" else round(value, 3)


def previous_run(run: EvaluationRun) -> EvaluationRun | None:
    """同じテナント・同じスイートの 1 つ前の実行。"""

    return (
        EvaluationRun.objects.filter(
            tenant=run.tenant,
            project=run.project,
            suite=run.suite,
            created_at__lte=run.created_at,
        )
        .exclude(pk=run.pk)
        .order_by("-created_at")
        .first()
    )


def metric_deltas(run: EvaluationRun | None) -> list[MetricDelta]:
    """指標ごとの現在値・前回値・差分。run が無くても定義だけは返す。"""

    before = previous_run(run) if run is not None else None
    rows: list[MetricDelta] = []

    for key, label, unit, definition in METRIC_DEFINITIONS:
        current = _scaled(key, unit, getattr(run, key, None) if run is not None else None)
        prior = _scaled(key, unit, getattr(before, key, None) if before is not None else None)
        delta = None if (current is None or prior is None) else round(current - prior, 3)
        rows.append(
            MetricDelta(
                key=key,
                label=label,
                unit=unit,
                definition=definition,
                current=current,
                previous=prior,
                delta=delta,
            )
        )

    return rows


def _retrieval_run(run: EvaluationRun, questions, *, use_vector: bool) -> list[EvaluationCase]:
    results = retrieval_service.evaluate_all(
        run.index, questions, top_k=run.top_k, use_vector=use_vector
    )
    scored = [result.metrics for result in results if result.evaluable and result.metrics]
    summary = metrics_service.aggregate(scored, reason=_no_score_reason(questions, results))

    run.evaluable = summary.evaluable
    run.case_count = len(results)
    run.unavailable_reason = "" if summary.evaluable else summary.reason
    run.recall_at_k = summary.recall_at_k
    run.precision_at_k = summary.precision_at_k
    run.mrr = summary.mrr
    run.pass_rate = (
        sum(1 for result in results if result.passed) / len(results) if results else None
    )
    run.issues = _flatten_issues(results)
    run.metrics = {"scored_cases": len(scored), "use_vector": use_vector}

    return [
        EvaluationCase(
            run=run,
            golden=result.golden,
            position=position,
            question=result.golden.question,
            evaluable=result.evaluable,
            passed=result.passed,
            first_hit_rank=result.metrics.first_hit_rank if result.metrics else None,
            recall=result.metrics.recall if result.metrics else None,
            precision=result.metrics.precision if result.metrics else None,
            reciprocal_rank=result.metrics.reciprocal_rank if result.metrics else None,
            matched_documents=list(result.detail.get("matched_titles", [])),
            missing_documents=list(result.detail.get("missing_titles", [])),
            issues=result.issues,
            detail=result.detail,
        )
        for position, result in enumerate(results, start=1)
    ]


def _no_score_reason(questions, results) -> str:
    if not questions:
        return metrics_service.NO_GOLDEN

    return "採点できる Golden がありません（期待文書の未設定・削除を確認してください）"


def _flatten_issues(results) -> list[str]:
    return [
        f"{result.golden.question[:30]}: {issue}" for result in results for issue in result.issues
    ]


def _answer_run(run: EvaluationRun, questions) -> list[EvaluationCase]:
    results = answer_service.evaluate_all(run.index, questions, top_k=run.top_k)

    run.evaluable = bool(results)
    run.case_count = len(results)
    run.unavailable_reason = "" if results else metrics_service.NO_GOLDEN
    run.pass_rate = (
        sum(1 for result in results if result.passed) / len(results) if results else None
    )
    run.issues = _flatten_issues(results)
    run.metrics = {
        "abstained": sum(1 for result in results if result.detail.get("abstained")),
        "with_citation": sum(1 for result in results if result.detail.get("citations")),
    }

    return [
        EvaluationCase(
            run=run,
            golden=result.golden,
            position=position,
            question=result.golden.question,
            evaluable=True,
            passed=result.passed,
            issues=result.issues,
            detail=result.detail,
        )
        for position, result in enumerate(results, start=1)
    ]


def _static_run(run: EvaluationRun, questions) -> list[EvaluationCase]:
    result = static_service.run_static_check(run.index, questions)

    run.evaluable = run.index is not None
    run.case_count = len(questions)
    run.unavailable_reason = "" if run.evaluable else "検索インデックスが未構築です"
    run.pass_rate = None if not run.evaluable else (1.0 if result.healthy else 0.0)
    run.issues = result.issues
    run.metrics = result.counts

    return []


@transaction.atomic
def run_evaluation(
    *,
    tenant,
    suite: str,
    project=None,
    user=None,
    top_k: int | None = None,
) -> EvaluationRun:
    """評価を 1 回実行し、履歴として保存する。

    外部 API は呼ばない。`local_hash` 既定でも語彙のみでも同じ結果経路を通る。
    """

    if suite not in EvaluationSuite.values:
        raise ValueError(f"未知の評価スイートです: {suite}")

    index = rag_selectors.current_index(tenant, project)
    questions = list(golden_service.golden_questions_for(tenant, project))
    run = EvaluationRun(
        tenant=tenant,
        project=project,
        index=index,
        executed_by=user if (user is not None and user.is_authenticated) else None,
        suite=suite,
        top_k=top_k or settings.RAG["DEFAULT_TOP_K"],
    )

    if suite == EvaluationSuite.STATIC:
        cases = _static_run(run, questions)
    elif suite == EvaluationSuite.ANSWER:
        cases = _answer_run(run, questions)
    else:
        cases = _retrieval_run(run, questions, use_vector=(suite == EvaluationSuite.RETRIEVAL))

    run.save()
    EvaluationCase.objects.bulk_create(cases)

    return run
