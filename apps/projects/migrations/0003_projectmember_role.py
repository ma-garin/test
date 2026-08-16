"""案件メンバーへ権限役割を持たせる。

既存データは `role_label` に「PMO」「参照」などの自由文字列が入っている。
既定値（メンバー）で一律に埋めると、参照専用だった人が編集できるようになり
権限が黙って緩む。表記から推定できるものは移行時に反映する。
"""

from django.db import migrations, models

from apps.accounts.constants import ROLE_LABEL_HINTS, ProjectRole


def fill_role_from_label(apps, schema_editor):
    """`role_label` の表記から役割を推定して埋める。"""

    ProjectMember = apps.get_model("projects", "ProjectMember")

    for member in ProjectMember.objects.exclude(role_label="").only("id", "role_label"):
        label = member.role_label

        for hint, role in ROLE_LABEL_HINTS:
            if hint in label:
                ProjectMember.objects.filter(pk=member.pk).update(role=role)
                break


def noop(apps, schema_editor):
    """役割から表記へは戻さない。表記は元から自由文字列で情報量が多い。"""


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0002_wbstask_actual_hours_wbstask_planned_hours"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectmember",
            name="role",
            field=models.CharField(
                choices=ProjectRole.choices,
                default=ProjectRole.MEMBER,
                max_length=16,
                verbose_name="権限役割",
            ),
        ),
        migrations.RunPython(fill_role_from_label, noop),
    ]
