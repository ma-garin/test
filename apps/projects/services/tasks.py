"""WBS タスクの更新系ロジック。

ビューから業務判断を追い出す。物理削除はしない（監査で「消えた経緯」を追えなく
なるため）。アーカイブは status を ARCHIVED へ倒すことで表現する。
"""

from __future__ import annotations

from django.db import transaction

from apps.projects.forms import WbsTaskForm
from apps.projects.models import WbsTask


@transaction.atomic
def create_task(form: WbsTaskForm) -> WbsTask:
    """新規タスクを保存する。フォームは検証済みであること。"""

    task: WbsTask = form.save(commit=False)
    task.save()
    form.save_m2m()

    return task


@transaction.atomic
def update_task(form: WbsTaskForm) -> WbsTask:
    """既存タスクを更新する。フォームは検証済みであること。"""

    task: WbsTask = form.save(commit=False)
    task.save()
    form.save_m2m()

    return task


@transaction.atomic
def archive_task(task: WbsTask) -> WbsTask:
    """タスクをアーカイブする（論理削除）。既にアーカイブ済みなら何もしない。"""

    if task.status == WbsTask.Status.ARCHIVED:
        return task

    task.status = WbsTask.Status.ARCHIVED
    task.save(update_fields=["status", "updated_at"])

    return task
