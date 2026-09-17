from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('scanner', '0044_material_request_rejection'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(model_name='purchaseitem', name='quantity_required', field=models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name='Требуемое количество')),
        migrations.AlterField(model_name='purchaseitem', name='quantity_purchased', field=models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name='Закупленное количество')),
        migrations.AlterField(model_name='purchaseitem', name='issued_quantity', field=models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name='Выдано')),
        migrations.AlterField(model_name='purchasetransaction', name='quantity', field=models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name='Количество')),
        migrations.AlterField(model_name='purchasetransaction', name='issued_quantity', field=models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name='Выдано')),
        migrations.AlterField(model_name='purchaserequestline', name='quantity_requested', field=models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name='Запрошено')),
        migrations.AlterField(model_name='purchaserequestline', name='quantity_issued', field=models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name='Выдано по заявке')),
        migrations.AddField(model_name='purchaserequest', name='cancellation_reason', field=models.CharField(blank=True, max_length=1000, verbose_name='Причина отмены')),
        migrations.AddField(model_name='purchaserequest', name='cancelled_at', field=models.DateTimeField(blank=True, null=True, verbose_name='Дата отмены')),
        migrations.AddField(model_name='purchaserequest', name='cancelled_by', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cancelled_purchase_requests', to=settings.AUTH_USER_MODEL, verbose_name='Отменил')),
    ]
