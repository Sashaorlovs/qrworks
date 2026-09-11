from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


def seed_material_grades(apps, schema_editor):
    Grade = apps.get_model("scanner", "MaterialGrade")
    values = [
        ("Сталь", "steel", Decimal("7850")),
        ("Ст3", "steel", Decimal("7850")),
        ("Сталь 20", "steel", Decimal("7850")),
        ("Сталь 45", "steel", Decimal("7850")),
        ("09Г2С", "steel", Decimal("7850")),
        ("12Х18Н10Т", "stainless", Decimal("7900")),
        ("Чугун", "cast_iron", Decimal("7200")),
        ("Алюминий", "aluminium", Decimal("2700")),
        ("Д16Т", "aluminium", Decimal("2780")),
        ("АМг6", "aluminium", Decimal("2640")),
        ("Медь", "copper", Decimal("8960")),
        ("Латунь", "brass", Decimal("8500")),
        ("Бронза", "bronze", Decimal("8800")),
        ("Титан", "titanium", Decimal("4500")),
    ]
    for name, category, density in values:
        Grade.objects.get_or_create(name=name, defaults={"category": category, "density_kg_m3": density})


class Migration(migrations.Migration):
    dependencies = [
        ("scanner", "0033_purchaseitem_issued_quantity_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MaterialGrade",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=160, unique=True, verbose_name="Марка материала")),
                ("category", models.CharField(choices=[("steel", "Сталь"), ("stainless", "Нержавеющая сталь"), ("cast_iron", "Чугун"), ("aluminium", "Алюминий и сплавы"), ("copper", "Медь"), ("brass", "Латунь"), ("bronze", "Бронза"), ("titanium", "Титан и сплавы"), ("other", "Прочее")], default="steel", max_length=20, verbose_name="Группа")),
                ("density_kg_m3", models.DecimalField(decimal_places=2, max_digits=8, validators=[MinValueValidator(Decimal("0.01"))], verbose_name="Плотность, кг/м³")),
                ("standard", models.CharField(blank=True, max_length=200, verbose_name="ГОСТ / стандарт")),
                ("is_active", models.BooleanField(default=True, verbose_name="Используется")),
            ],
            options={"verbose_name": "Марка складского материала", "verbose_name_plural": "Марки складских материалов", "ordering": ["category", "name"]},
        ),
        migrations.CreateModel(
            name="MaterialRequirement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("profile_type", models.CharField(choices=[("sheet", "Лист"), ("round_pipe", "Труба круглая"), ("rect_tube", "Труба профильная"), ("round_bar", "Круг"), ("square_bar", "Квадрат"), ("rect_bar", "Полоса / прямоугольник"), ("angle", "Уголок"), ("channel", "Швеллер"), ("beam", "Балка"), ("other", "Другой профиль")], max_length=20, verbose_name="Профиль")),
                ("profile_name", models.CharField(blank=True, max_length=220, verbose_name="Обозначение профиля / сортамент")),
                ("thickness_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True, verbose_name="Толщина, мм")),
                ("width_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Ширина, мм")),
                ("height_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Высота, мм")),
                ("outer_diameter_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Наружный диаметр, мм")),
                ("wall_thickness_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True, verbose_name="Стенка, мм")),
                ("piece_length_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True, verbose_name="Длина единицы, мм")),
                ("kg_per_meter", models.DecimalField(blank=True, decimal_places=5, max_digits=12, null=True, verbose_name="Масса 1 м, кг")),
                ("unit_mass_kg", models.DecimalField(blank=True, decimal_places=5, max_digits=14, null=True, verbose_name="Масса единицы, кг")),
                ("assembly_name", models.CharField(blank=True, max_length=500, verbose_name="Узел / подсборка")),
                ("item_name", models.CharField(max_length=500, verbose_name="Материал / назначение")),
                ("quantity_required", models.DecimalField(decimal_places=3, default=0, max_digits=14, verbose_name="Требуется, шт")),
                ("total_length_required_mm", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Требуется длины, мм")),
                ("calculated_area_m2", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Площадь, м²")),
                ("calculated_mass_kg", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Расчётная масса, кг")),
                ("notes", models.TextField(blank=True, verbose_name="Примечание")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("assembly_ref", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="material_requirements", to="scanner.orderitem", verbose_name="Узел")),
                ("grade", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="scanner.materialgrade", verbose_name="Марка материала")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="material_requirements", to="scanner.order", verbose_name="Проект / заказ")),
            ],
            options={"verbose_name": "Потребность в материале", "verbose_name_plural": "Потребности в материалах", "ordering": ["order", "assembly_name", "item_name"]},
        ),
        migrations.CreateModel(
            name="MaterialStockLot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("profile_type", models.CharField(choices=[("sheet", "Лист"), ("round_pipe", "Труба круглая"), ("rect_tube", "Труба профильная"), ("round_bar", "Круг"), ("square_bar", "Квадрат"), ("rect_bar", "Полоса / прямоугольник"), ("angle", "Уголок"), ("channel", "Швеллер"), ("beam", "Балка"), ("other", "Другой профиль")], max_length=20, verbose_name="Профиль")),
                ("profile_name", models.CharField(blank=True, max_length=220, verbose_name="Обозначение профиля / сортамент")),
                ("thickness_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True, verbose_name="Толщина, мм")),
                ("width_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Ширина, мм")),
                ("height_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Высота, мм")),
                ("outer_diameter_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Наружный диаметр, мм")),
                ("wall_thickness_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True, verbose_name="Стенка, мм")),
                ("piece_length_mm", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True, verbose_name="Длина единицы, мм")),
                ("kg_per_meter", models.DecimalField(blank=True, decimal_places=5, max_digits=12, null=True, verbose_name="Масса 1 м, кг")),
                ("unit_mass_kg", models.DecimalField(blank=True, decimal_places=5, max_digits=14, null=True, verbose_name="Масса единицы, кг")),
                ("name", models.CharField(max_length=500, verbose_name="Наименование")),
                ("batch_number", models.CharField(blank=True, max_length=120, verbose_name="Партия / плавка")),
                ("storage_location", models.CharField(blank=True, max_length=180, verbose_name="Место хранения")),
                ("quantity_initial", models.DecimalField(decimal_places=3, default=0, max_digits=14, verbose_name="Принято, шт")),
                ("quantity_remaining", models.DecimalField(decimal_places=3, default=0, max_digits=14, verbose_name="Остаток, шт")),
                ("length_initial_mm", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Принято длины, мм")),
                ("length_remaining_mm", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Остаток длины, мм")),
                ("mass_initial_kg", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Принято, кг")),
                ("mass_remaining_kg", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Остаток, кг")),
                ("received_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата прихода")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name="Принял")),
                ("grade", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="scanner.materialgrade", verbose_name="Марка материала")),
                ("order", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="material_stock_lots", to="scanner.order", verbose_name="Проект / заказ")),
            ],
            options={"verbose_name": "Партия материала на складе", "verbose_name_plural": "Партии материалов на складе", "ordering": ["grade__name", "profile_type", "name", "received_at"]},
        ),
        migrations.CreateModel(
            name="MaterialRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("number", models.CharField(max_length=40, unique=True, verbose_name="Номер заявки")),
                ("purpose", models.CharField(blank=True, max_length=300, verbose_name="Основание / назначение")),
                ("status", models.CharField(choices=[("open", "Открыта"), ("partial", "Выдано частично"), ("issued", "Выдано"), ("cancelled", "Отменена")], default="open", max_length=12, verbose_name="Статус")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="material_requests", to="scanner.order", verbose_name="Проект / заказ")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name="Сформировал")),
            ],
            options={"verbose_name": "Заявка на материал", "verbose_name_plural": "Заявки на материалы", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="MaterialRequestLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity_requested", models.DecimalField(decimal_places=3, default=0, max_digits=14, verbose_name="Запрошено, шт")),
                ("length_requested_mm", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Запрошено длины, мм")),
                ("mass_requested_kg", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Запрошено, кг")),
                ("quantity_issued", models.DecimalField(decimal_places=3, default=0, max_digits=14, verbose_name="Выдано, шт")),
                ("length_issued_mm", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Выдано длины, мм")),
                ("mass_issued_kg", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Выдано, кг")),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="scanner.materialrequest")),
                ("requirement", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="request_lines", to="scanner.materialrequirement")),
            ],
            options={"verbose_name": "Строка заявки на материал", "verbose_name_plural": "Строки заявок на материалы", "ordering": ["id"]},
        ),
        migrations.CreateModel(
            name="MaterialTransaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("transaction_type", models.CharField(choices=[("in", "Приход"), ("out", "Выдача"), ("return", "Возврат"), ("adjustment", "Корректировка")], max_length=12, verbose_name="Тип")),
                ("quantity", models.DecimalField(decimal_places=3, default=0, max_digits=14, verbose_name="Количество, шт")),
                ("length_mm", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Длина, мм")),
                ("mass_kg", models.DecimalField(decimal_places=3, default=0, max_digits=16, verbose_name="Масса, кг")),
                ("recipient_name", models.CharField(blank=True, max_length=255, verbose_name="Получатель (на момент выдачи)")),
                ("basis", models.CharField(blank=True, max_length=300, verbose_name="Основание")),
                ("batch_token", models.CharField(blank=True, db_index=True, max_length=64, verbose_name="Группа накладной")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name="Выдал / принял")),
                ("order", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="material_transactions", to="scanner.order", verbose_name="Проект / заказ")),
                ("recipient", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="material_receipts", to="scanner.employee", verbose_name="Получатель")),
                ("request_line", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="transactions", to="scanner.materialrequestline", verbose_name="Строка заявки")),
                ("stock_lot", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transactions", to="scanner.materialstocklot", verbose_name="Партия материала")),
            ],
            options={"verbose_name": "Движение материала", "verbose_name_plural": "Движения материалов", "ordering": ["-created_at"]},
        ),
        migrations.RunPython(seed_material_grades, migrations.RunPython.noop),
    ]
