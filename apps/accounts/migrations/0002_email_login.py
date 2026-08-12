"""ログイン識別子をメールアドレスへ移す。

`email` に unique を付ける前に、空欄のまま残っている利用者を埋める。
埋めないと制約追加で失敗する。
"""

from django.db import migrations, models


def fill_blank_emails(apps, schema_editor):
    User = apps.get_model("accounts", "User")

    for pk, username in User.objects.filter(email="").values_list("pk", "username"):
        User.objects.filter(pk=pk).update(email=f"{username}@example.com")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(fill_blank_emails, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(max_length=254, unique=True, verbose_name="メールアドレス"),
        ),
    ]
