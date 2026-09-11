from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('scanner', '0035_purchase_online_documents'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchasetransaction',
            name='is_general_use',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Общепроизводственные нужды'),
        ),
    ]
