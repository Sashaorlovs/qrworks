from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('scanner', '0034_material_warehouse'),
    ]

    operations = [
        migrations.CreateModel(
            name='PurchaseRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.CharField(max_length=32, unique=True, verbose_name='Номер заявки')),
                ('purpose', models.CharField(blank=True, max_length=255, verbose_name='Основание / назначение')),
                ('status', models.CharField(choices=[('open', 'Ожидает выдачи'), ('partial', 'Выдана частично'), ('issued', 'Выдана полностью'), ('cancelled', 'Отменена')], db_index=True, default='open', max_length=16, verbose_name='Статус')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Дата')),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='purchase_requests', to='scanner.order', verbose_name='Проект / заказ')),
                ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name='Сформировал')),
            ],
            options={'verbose_name': 'Заявка на стандартные изделия', 'verbose_name_plural': 'Заявки на стандартные изделия', 'ordering': ['-created_at', '-id']},
        ),
        migrations.CreateModel(
            name='PurchaseRequestLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity_requested', models.PositiveIntegerField(default=0, verbose_name='Запрошено')),
                ('quantity_issued', models.PositiveIntegerField(default=0, verbose_name='Выдано по заявке')),
                ('purchase_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='request_lines', to='scanner.purchaseitem', verbose_name='Позиция спецификации')),
                ('request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='scanner.purchaserequest')),
            ],
            options={'verbose_name': 'Строка заявки на стандартные изделия', 'verbose_name_plural': 'Строки заявок на стандартные изделия', 'ordering': ['purchase_item__item_name', 'id']},
        ),
        migrations.AddField(
            model_name='purchasetransaction',
            name='document_number',
            field=models.CharField(blank=True, db_index=True, max_length=32, verbose_name='Номер накладной'),
        ),
        migrations.AddField(
            model_name='purchasetransaction',
            name='request_line',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transactions', to='scanner.purchaserequestline', verbose_name='Строка заявки'),
        ),
    ]
