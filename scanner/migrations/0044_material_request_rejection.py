from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("scanner", "0043_material_requirement_destination"),
    ]

    operations = [
        migrations.AlterField(
            model_name="materialrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("open", "Открыта"),
                    ("partial", "Выдано частично"),
                    ("issued", "Выдано"),
                    ("cancelled", "Отклонена"),
                ],
                default="open",
                max_length=12,
                verbose_name="Статус",
            ),
        ),
        migrations.AddField(
            model_name="materialrequest",
            name="rejected_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Дата отклонения"),
        ),
        migrations.AddField(
            model_name="materialrequest",
            name="rejected_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="rejected_material_requests",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Отклонил",
            ),
        ),
        migrations.AddField(
            model_name="materialrequest",
            name="rejection_reason",
            field=models.TextField(blank=True, verbose_name="Причина отклонения"),
        ),
    ]
