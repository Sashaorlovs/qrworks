from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("scanner", "0041_purchaseitem_normalized_name"),
    ]

    operations = [
        migrations.AlterField(
            model_name="materialrequirement",
            name="order",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="material_requirements",
                to="scanner.order",
                verbose_name="Проект / заказ",
            ),
        ),
        migrations.AlterField(
            model_name="materialrequest",
            name="order",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="material_requests",
                to="scanner.order",
                verbose_name="Проект / заказ",
            ),
        ),
        migrations.AlterField(
            model_name="auxiliarymaterialrequirement",
            name="order",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="auxiliary_material_requirements",
                to="scanner.order",
                verbose_name="Проект / заказ",
            ),
        ),
    ]
