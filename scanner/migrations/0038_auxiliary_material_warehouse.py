from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("scanner", "0037_purchasetransaction_issuer_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuxiliaryMaterialLot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField(choices=[("paint", "Краска / покрытие"), ("solvent", "Растворитель"), ("lubricant", "Смазка / масло"), ("rubber", "Резина / прокладочный материал"), ("mesh", "Сетка"), ("bulk", "Сыпучий материал"), ("piece", "Штучный расходный материал"), ("other", "Прочее")], db_index=True, max_length=20, verbose_name="Категория")),
                ("name", models.CharField(max_length=500, verbose_name="Наименование")),
                ("brand", models.CharField(blank=True, max_length=220, verbose_name="Марка / производитель")),
                ("characteristics", models.CharField(blank=True, max_length=500, verbose_name="Характеристика")),
                ("unit", models.CharField(choices=[("kg", "кг"), ("g", "г"), ("l", "л"), ("ml", "мл"), ("pcs", "шт."), ("m", "м"), ("m2", "м²"), ("roll", "рулон"), ("pack", "упаковка")], max_length=10, verbose_name="Единица учёта")),
                ("quantity_initial", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Принято")),
                ("quantity_remaining", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Остаток")),
                ("package_description", models.CharField(blank=True, max_length=220, verbose_name="Тара / упаковка")),
                ("density_kg_l", models.DecimalField(blank=True, decimal_places=5, max_digits=10, null=True, verbose_name="Плотность, кг/л")),
                ("thickness_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Толщина, мм")),
                ("width_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True, verbose_name="Ширина, мм")),
                ("length_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=16, null=True, verbose_name="Длина, мм")),
                ("mesh_cell_width_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Ячейка по ширине, мм")),
                ("mesh_cell_height_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Ячейка по высоте, мм")),
                ("wire_diameter_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Диаметр проволоки, мм")),
                ("batch_number", models.CharField(blank=True, max_length=160, verbose_name="Партия")),
                ("expiry_date", models.DateField(blank=True, null=True, verbose_name="Срок годности")),
                ("storage_location", models.CharField(blank=True, max_length=180, verbose_name="Место хранения")),
                ("hazardous", models.BooleanField(default=False, verbose_name="Опасный материал / ЛВЖ")),
                ("minimum_stock", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Минимальный остаток")),
                ("received_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата прихода")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name="Принял")),
                ("order", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="auxiliary_material_lots", to="scanner.order", verbose_name="Проект / заказ")),
            ],
            options={"verbose_name": "Прочий материал на складе", "verbose_name_plural": "Прочие материалы на складе", "ordering": ["category", "name", "brand", "received_at"]},
        ),
        migrations.CreateModel(
            name="AuxiliaryMaterialTransaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("transaction_type", models.CharField(choices=[("in", "Приход"), ("out", "Выдача"), ("adjustment", "Корректировка")], max_length=12, verbose_name="Тип")),
                ("quantity", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Количество")),
                ("recipient_name", models.CharField(blank=True, max_length=255, verbose_name="Получатель (на момент выдачи)")),
                ("basis", models.CharField(blank=True, max_length=300, verbose_name="Основание")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name="Выдал / принял")),
                ("recipient", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="auxiliary_material_receipts", to="scanner.employee", verbose_name="Получатель")),
                ("stock_lot", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transactions", to="scanner.auxiliarymateriallot", verbose_name="Материал")),
            ],
            options={"verbose_name": "Движение прочего материала", "verbose_name_plural": "Движения прочих материалов", "ordering": ["-created_at", "-id"]},
        ),
    ]
