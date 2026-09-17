from django.db import migrations, models


ISSUERS = [
    "Макарова Оксана Михайловна",
    "Чувашлева Екатерина Юрьевна",
]


def add_issuers(apps, schema_editor):
    WarehouseIssuer = apps.get_model("scanner", "WarehouseIssuer")
    for name in ISSUERS:
        WarehouseIssuer.objects.get_or_create(name=name, defaults={"is_active": True})


def remove_issuers(apps, schema_editor):
    WarehouseIssuer = apps.get_model("scanner", "WarehouseIssuer")
    WarehouseIssuer.objects.filter(name__in=ISSUERS).delete()


class Migration(migrations.Migration):
    dependencies = [("scanner", "0046_purchase_preparations")]

    operations = [
        migrations.CreateModel(
            name="WarehouseIssuer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255, unique=True, verbose_name="ФИО сотрудника склада")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="Доступен для выбора")),
            ],
            options={
                "ordering": ["name"],
                "verbose_name": "Сотрудник, отпускающий материал",
                "verbose_name_plural": "Сотрудники, отпускающие материал",
            },
        ),
        migrations.AddField(
            model_name="materialtransaction",
            name="issuer_name",
            field=models.CharField(blank=True, max_length=255, verbose_name="Кто отпустил (ФИО)"),
        ),
        migrations.AddField(
            model_name="auxiliarymaterialtransaction",
            name="issuer_name",
            field=models.CharField(blank=True, max_length=255, verbose_name="Кто отпустил (ФИО)"),
        ),
        migrations.RunPython(add_issuers, remove_issuers),
    ]
