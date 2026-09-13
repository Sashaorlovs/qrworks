from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("scanner", "0039_material_profile_choices")]

    operations = [
        migrations.CreateModel(
            name="AuxiliaryMaterialRequirement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("assembly_name", models.CharField(blank=True, max_length=500, verbose_name="Узел / подсборка")),
                ("category", models.CharField(choices=[("paint", "Краска / покрытие"), ("solvent", "Растворитель"), ("lubricant", "Смазка / масло"), ("rubber", "Резина / прокладочный материал"), ("mesh", "Сетка"), ("bulk", "Сыпучий материал"), ("piece", "Штучный расходный материал"), ("other", "Прочее")], db_index=True, max_length=20, verbose_name="Категория")),
                ("name", models.CharField(max_length=500, verbose_name="Наименование")),
                ("brand", models.CharField(blank=True, max_length=220, verbose_name="Марка / производитель")),
                ("characteristics", models.CharField(blank=True, max_length=500, verbose_name="Характеристика")),
                ("unit", models.CharField(choices=[("kg", "кг"), ("g", "г"), ("l", "л"), ("ml", "мл"), ("pcs", "шт."), ("m", "м"), ("m2", "м²"), ("roll", "рулон"), ("pack", "упаковка")], max_length=10, verbose_name="Единица учёта")),
                ("quantity_required", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Требуется")),
                ("package_description", models.CharField(blank=True, max_length=220, verbose_name="Тара / упаковка")),
                ("density_kg_l", models.DecimalField(blank=True, decimal_places=5, max_digits=10, null=True, verbose_name="Плотность, кг/л")),
                ("thickness_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Толщина, мм")),
                ("width_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True, verbose_name="Ширина, мм")),
                ("length_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=16, null=True, verbose_name="Длина, мм")),
                ("mesh_cell_width_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Ячейка по ширине, мм")),
                ("mesh_cell_height_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Ячейка по высоте, мм")),
                ("wire_diameter_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Диаметр проволоки, мм")),
                ("notes", models.TextField(blank=True, verbose_name="Примечание")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="auxiliary_material_requirements", to="scanner.order", verbose_name="Проект / заказ")),
            ],
            options={"verbose_name": "Потребность в прочем материале", "verbose_name_plural": "Потребности в прочих материалах", "ordering": ["order", "assembly_name", "category", "name"]},
        ),
    ]
