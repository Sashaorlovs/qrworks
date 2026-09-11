from django.db import migrations, models


def renumber_existing_invoices(apps, schema_editor):
    PurchaseTransaction = apps.get_model('scanner', 'PurchaseTransaction')
    PurchaseInvoiceCounter = apps.get_model('scanner', 'PurchaseInvoiceCounter')

    rows = list(
        PurchaseTransaction.objects.filter(transaction_type='out')
        .order_by('created_at', 'id')
        .values('id', 'batch_token', 'created_at')
    )
    groups = {}
    for row in rows:
        key = f"batch:{row['batch_token']}" if row['batch_token'] else f"row:{row['id']}"
        groups.setdefault(key, []).append(row)

    counters = {}
    ordered_groups = sorted(
        groups.values(),
        key=lambda group: (group[0]['created_at'], group[0]['id']),
    )
    for group in ordered_groups:
        year = group[0]['created_at'].year
        counters[year] = counters.get(year, 0) + 1
        number = f'НК-{year}-{counters[year]:06d}'
        PurchaseTransaction.objects.filter(
            id__in=[row['id'] for row in group]
        ).update(document_number=number)

    PurchaseInvoiceCounter.objects.bulk_create([
        PurchaseInvoiceCounter(year=year, last_number=last_number)
        for year, last_number in counters.items()
    ])


class Migration(migrations.Migration):
    dependencies = [
        ('scanner', '0036_purchasetransaction_is_general_use'),
    ]

    operations = [
        migrations.CreateModel(
            name='PurchaseInvoiceCounter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveIntegerField(unique=True, verbose_name='Год')),
                ('last_number', models.PositiveIntegerField(default=0, verbose_name='Последний номер')),
            ],
            options={
                'verbose_name': 'Счётчик накладных стандартных изделий',
                'verbose_name_plural': 'Счётчики накладных стандартных изделий',
            },
        ),
        migrations.AddField(
            model_name='purchasetransaction',
            name='issuer_name',
            field=models.CharField(blank=True, max_length=255, verbose_name='Кто выдал (ФИО)'),
        ),
        migrations.RunPython(renumber_existing_invoices, migrations.RunPython.noop),
    ]
