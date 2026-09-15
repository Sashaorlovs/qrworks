from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scanner", "0042_general_material_requirements"),
    ]

    operations = [
        migrations.AddField(
            model_name="materialrequirement",
            name="destination",
            field=models.CharField(blank=True, max_length=300, verbose_name="Назначение без проекта"),
        ),
        migrations.AddField(
            model_name="materialrequest",
            name="destination",
            field=models.CharField(blank=True, max_length=300, verbose_name="Назначение без проекта"),
        ),
        migrations.AddField(
            model_name="auxiliarymaterialrequirement",
            name="destination",
            field=models.CharField(blank=True, max_length=300, verbose_name="Назначение без проекта"),
        ),
    ]
