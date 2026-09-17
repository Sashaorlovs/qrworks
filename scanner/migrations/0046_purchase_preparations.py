from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('scanner', '0045_purchase_decimal_quantities_and_cancellation'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PurchasePreparation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True, verbose_name='Название предварительной ведомости')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создана')),
                ('assigned_at', models.DateTimeField(blank=True, null=True, verbose_name='Дата привязки')),
                ('assigned_order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='purchase_preparations', to='scanner.order', verbose_name='Привязанный заказ')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name='Создал')),
            ],
            options={'verbose_name': 'Предварительная ведомость крепежа', 'verbose_name_plural': 'Предварительные ведомости крепежа', 'ordering': ['-created_at', '-id']},
        ),
        migrations.AddField(
            model_name='purchaseitem', name='preparation',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='items', to='scanner.purchasepreparation', verbose_name='Предварительная ведомость'),
        ),
    ]
