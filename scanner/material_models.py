from decimal import Decimal, ROUND_HALF_UP
from math import pi, sqrt

from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models


ZERO = Decimal("0")
MM3_IN_M3 = Decimal("1000000000")


class MaterialGrade(models.Model):
    CATEGORY_CHOICES = [
        ("steel", "Сталь"),
        ("stainless", "Нержавеющая сталь"),
        ("cast_iron", "Чугун"),
        ("aluminium", "Алюминий и сплавы"),
        ("copper", "Медь"),
        ("brass", "Латунь"),
        ("bronze", "Бронза"),
        ("titanium", "Титан и сплавы"),
        ("other", "Прочее"),
    ]

    name = models.CharField(max_length=160, unique=True, verbose_name="Марка материала")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="steel", verbose_name="Группа")
    density_kg_m3 = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name="Плотность, кг/м³",
    )
    standard = models.CharField(max_length=200, blank=True, verbose_name="ГОСТ / стандарт")
    is_active = models.BooleanField(default=True, verbose_name="Используется")

    class Meta:
        ordering = ["category", "name"]
        verbose_name = "Марка складского материала"
        verbose_name_plural = "Марки складских материалов"

    def __str__(self):
        return f"{self.name} — {self.density_kg_m3} кг/м³"


class MaterialGeometry(models.Model):
    PROFILE_CHOICES = [
        ("sheet", "Лист"),
        ("round_pipe", "Труба круглая"),
        ("rect_tube", "Труба профильная"),
        ("round_bar", "Круг"),
        ("square_bar", "Квадрат"),
        ("hex_bar", "Шестигранник"),
        ("rect_bar", "Полоса / прямоугольник"),
        ("angle", "Уголок"),
        ("channel", "Швеллер"),
        ("beam", "Двутавр / балка"),
        ("bulb_flat", "Полособульб"),
        ("other", "Другой профиль"),
    ]
    LINEAR_PROFILES = {"round_pipe", "rect_tube", "round_bar", "square_bar", "hex_bar", "rect_bar", "angle", "channel", "beam", "bulb_flat", "other"}

    grade = models.ForeignKey(MaterialGrade, on_delete=models.PROTECT, verbose_name="Марка материала")
    profile_type = models.CharField(max_length=20, choices=PROFILE_CHOICES, verbose_name="Профиль")
    profile_name = models.CharField(max_length=220, blank=True, verbose_name="Обозначение профиля / сортамент")
    thickness_mm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, verbose_name="Толщина, мм")
    width_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, verbose_name="Ширина, мм")
    height_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, verbose_name="Высота, мм")
    outer_diameter_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, verbose_name="Наружный диаметр, мм")
    wall_thickness_mm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, verbose_name="Стенка, мм")
    piece_length_mm = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True, verbose_name="Длина единицы, мм")
    kg_per_meter = models.DecimalField(max_digits=12, decimal_places=5, null=True, blank=True, verbose_name="Масса 1 м, кг")
    unit_mass_kg = models.DecimalField(max_digits=14, decimal_places=5, null=True, blank=True, verbose_name="Масса единицы, кг")

    class Meta:
        abstract = True

    @property
    def is_linear(self):
        return self.profile_type in self.LINEAR_PROFILES

    def geometry_signature(self):
        def value(field):
            raw = getattr(self, field)
            return str(raw.normalize()) if raw is not None else ""

        return (
            self.grade_id,
            self.profile_type,
            (self.profile_name or "").strip().casefold(),
            value("thickness_mm"),
            value("width_mm"),
            value("height_mm"),
            value("outer_diameter_mm"),
            value("wall_thickness_mm"),
            value("piece_length_mm"),
            value("kg_per_meter"),
            value("unit_mass_kg"),
        )

    def cross_section_mm2(self):
        def dec(value):
            return Decimal(str(value)) if value not in (None, "") else ZERO

        d = dec(self.outer_diameter_mm)
        s = dec(self.wall_thickness_mm or self.thickness_mm)
        w = dec(self.width_mm)
        h = dec(self.height_mm)

        if self.profile_type == "round_pipe" and d > 0 and s > 0 and d > s * 2:
            inner = d - s * 2
            return Decimal(str(pi)) * (d * d - inner * inner) / Decimal("4")
        if self.profile_type == "rect_tube" and w > 0 and h > 0 and s > 0 and w > s * 2 and h > s * 2:
            return w * h - (w - s * 2) * (h - s * 2)
        if self.profile_type == "round_bar" and d > 0:
            return Decimal(str(pi)) * d * d / Decimal("4")
        if self.profile_type == "square_bar" and w > 0:
            return w * w
        if self.profile_type == "hex_bar" and w > 0:
            return Decimal(str(sqrt(3))) * w * w / Decimal("2")
        if self.profile_type == "rect_bar" and w > 0 and (h > 0 or s > 0):
            return w * (h or s)
        return None

    def calculate_mass(self, quantity=ZERO, total_length_mm=ZERO):
        quantity = Decimal(str(quantity or 0))
        total_length_mm = Decimal(str(total_length_mm or 0))
        density = self.grade.density_kg_m3

        if self.unit_mass_kg is not None and quantity > 0:
            mass = self.unit_mass_kg * quantity
        elif self.profile_type == "sheet":
            t = self.thickness_mm or ZERO
            w = self.width_mm or ZERO
            length = self.piece_length_mm or ZERO
            mass = t * w * length * quantity * density / MM3_IN_M3
        else:
            if total_length_mm <= 0 and self.piece_length_mm and quantity > 0:
                total_length_mm = self.piece_length_mm * quantity
            if self.kg_per_meter is not None and total_length_mm > 0:
                mass = self.kg_per_meter * total_length_mm / Decimal("1000")
            else:
                section = self.cross_section_mm2()
                mass = (section * total_length_mm * density / MM3_IN_M3) if section and total_length_mm > 0 else ZERO
        return mass.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    def calculate_area_m2(self, quantity=ZERO):
        if self.profile_type != "sheet":
            return ZERO
        quantity = Decimal(str(quantity or 0))
        width = self.width_mm or ZERO
        length = self.piece_length_mm or ZERO
        return (width * length * quantity / Decimal("1000000")).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    def dimensions_display(self):
        if self.profile_name:
            return self.profile_name
        if self.profile_type == "sheet":
            return f"{self.thickness_mm or '—'} × {self.width_mm or '—'} × {self.piece_length_mm or '—'} мм"
        if self.profile_type == "round_pipe":
            return f"Ø{self.outer_diameter_mm or '—'} × {self.wall_thickness_mm or '—'}, L={self.piece_length_mm or '—'} мм"
        if self.profile_type == "rect_tube":
            return f"{self.width_mm or '—'} × {self.height_mm or '—'} × {self.wall_thickness_mm or '—'}, L={self.piece_length_mm or '—'} мм"
        if self.profile_type == "round_bar":
            return f"Ø{self.outer_diameter_mm or '—'}, L={self.piece_length_mm or '—'} мм"
        if self.profile_type == "hex_bar":
            return f"S{self.width_mm or '—'}, L={self.piece_length_mm or '—'} мм"
        if self.profile_type == "square_bar":
            return f"{self.width_mm or '—'} × {self.width_mm or '—'}, L={self.piece_length_mm or '—'} мм"
        if self.profile_type == "rect_bar":
            return f"{self.width_mm or '—'} × {self.height_mm or self.thickness_mm or '—'}, L={self.piece_length_mm or '—'} мм"
        return f"L={self.piece_length_mm or '—'} мм"


class MaterialRequirement(MaterialGeometry):
    order = models.ForeignKey("scanner.Order", on_delete=models.CASCADE, related_name="material_requirements", verbose_name="Проект / заказ")
    assembly_ref = models.ForeignKey("scanner.OrderItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="material_requirements", verbose_name="Узел")
    assembly_name = models.CharField(max_length=500, blank=True, verbose_name="Узел / подсборка")
    item_name = models.CharField(max_length=500, verbose_name="Материал / назначение")
    quantity_required = models.DecimalField(max_digits=14, decimal_places=3, default=0, verbose_name="Требуется, шт")
    total_length_required_mm = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Требуется длины, мм")
    calculated_area_m2 = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Площадь, м²")
    calculated_mass_kg = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Расчётная масса, кг")
    notes = models.TextField(blank=True, verbose_name="Примечание")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "assembly_name", "item_name"]
        verbose_name = "Потребность в материале"
        verbose_name_plural = "Потребности в материалах"

    def save(self, *args, **kwargs):
        if self.is_linear and not self.total_length_required_mm and self.piece_length_mm:
            self.total_length_required_mm = self.piece_length_mm * self.quantity_required
        self.calculated_area_m2 = self.calculate_area_m2(self.quantity_required)
        self.calculated_mass_kg = self.calculate_mass(self.quantity_required, self.total_length_required_mm)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order}: {self.item_name}"


class MaterialStockLot(MaterialGeometry):
    order = models.ForeignKey("scanner.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="material_stock_lots", verbose_name="Проект / заказ")
    name = models.CharField(max_length=500, verbose_name="Наименование")
    batch_number = models.CharField(max_length=120, blank=True, verbose_name="Партия / плавка")
    storage_location = models.CharField(max_length=180, blank=True, verbose_name="Место хранения")
    quantity_initial = models.DecimalField(max_digits=14, decimal_places=3, default=0, verbose_name="Принято, шт")
    quantity_remaining = models.DecimalField(max_digits=14, decimal_places=3, default=0, verbose_name="Остаток, шт")
    length_initial_mm = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Принято длины, мм")
    length_remaining_mm = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Остаток длины, мм")
    mass_initial_kg = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Принято, кг")
    mass_remaining_kg = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Остаток, кг")
    received_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата прихода")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Принял")

    class Meta:
        ordering = ["grade__name", "profile_type", "name", "received_at"]
        verbose_name = "Партия материала на складе"
        verbose_name_plural = "Партии материалов на складе"

    def initialize_balances(self):
        if self.is_linear and not self.length_initial_mm and self.piece_length_mm:
            self.length_initial_mm = self.piece_length_mm * self.quantity_initial
        self.quantity_remaining = self.quantity_initial
        self.length_remaining_mm = self.length_initial_mm
        self.mass_initial_kg = self.calculate_mass(self.quantity_initial, self.length_initial_mm)
        self.mass_remaining_kg = self.mass_initial_kg

    def __str__(self):
        return f"{self.name} ({self.grade.name})"


class MaterialRequest(models.Model):
    STATUS_CHOICES = [("open", "Открыта"), ("partial", "Выдано частично"), ("issued", "Выдано"), ("cancelled", "Отменена")]

    number = models.CharField(max_length=40, unique=True, verbose_name="Номер заявки")
    order = models.ForeignKey("scanner.Order", on_delete=models.CASCADE, related_name="material_requests", verbose_name="Проект / заказ")
    purpose = models.CharField(max_length=300, blank=True, verbose_name="Основание / назначение")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="open", verbose_name="Статус")
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Сформировал")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Заявка на материал"
        verbose_name_plural = "Заявки на материалы"

    def __str__(self):
        return self.number


class MaterialRequestLine(models.Model):
    request = models.ForeignKey(MaterialRequest, on_delete=models.CASCADE, related_name="lines")
    requirement = models.ForeignKey(MaterialRequirement, on_delete=models.PROTECT, related_name="request_lines")
    quantity_requested = models.DecimalField(max_digits=14, decimal_places=3, default=0, verbose_name="Запрошено, шт")
    length_requested_mm = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Запрошено длины, мм")
    mass_requested_kg = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Запрошено, кг")
    quantity_issued = models.DecimalField(max_digits=14, decimal_places=3, default=0, verbose_name="Выдано, шт")
    length_issued_mm = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Выдано длины, мм")
    mass_issued_kg = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Выдано, кг")

    class Meta:
        ordering = ["id"]
        verbose_name = "Строка заявки на материал"
        verbose_name_plural = "Строки заявок на материалы"

    @property
    def quantity_remaining(self):
        return max(ZERO, self.quantity_requested - self.quantity_issued)

    @property
    def length_remaining_mm(self):
        return max(ZERO, self.length_requested_mm - self.length_issued_mm)


class MaterialTransaction(models.Model):
    TRANSACTION_TYPES = [("in", "Приход"), ("out", "Выдача"), ("return", "Возврат"), ("adjustment", "Корректировка")]

    stock_lot = models.ForeignKey(MaterialStockLot, on_delete=models.PROTECT, related_name="transactions", verbose_name="Партия материала")
    request_line = models.ForeignKey(MaterialRequestLine, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions", verbose_name="Строка заявки")
    order = models.ForeignKey("scanner.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="material_transactions", verbose_name="Проект / заказ")
    transaction_type = models.CharField(max_length=12, choices=TRANSACTION_TYPES, verbose_name="Тип")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0, verbose_name="Количество, шт")
    length_mm = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Длина, мм")
    mass_kg = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Масса, кг")
    recipient = models.ForeignKey("scanner.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="material_receipts", verbose_name="Получатель")
    recipient_name = models.CharField(max_length=255, blank=True, verbose_name="Получатель (на момент выдачи)")
    basis = models.CharField(max_length=300, blank=True, verbose_name="Основание")
    batch_token = models.CharField(max_length=64, blank=True, db_index=True, verbose_name="Группа накладной")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Выдал / принял")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Движение материала"
        verbose_name_plural = "Движения материалов"

    def __str__(self):
        return f"{self.get_transaction_type_display()} — {self.stock_lot.name}"


class AuxiliaryMaterialLot(models.Model):
    CATEGORY_CHOICES = [
        ("paint", "Краска / покрытие"),
        ("solvent", "Растворитель"),
        ("lubricant", "Смазка / масло"),
        ("rubber", "Резина / прокладочный материал"),
        ("mesh", "Сетка"),
        ("bulk", "Сыпучий материал"),
        ("piece", "Штучный расходный материал"),
        ("other", "Прочее"),
    ]
    UNIT_CHOICES = [
        ("kg", "кг"),
        ("g", "г"),
        ("l", "л"),
        ("ml", "мл"),
        ("pcs", "шт."),
        ("m", "м"),
        ("m2", "м²"),
        ("roll", "рулон"),
        ("pack", "упаковка"),
    ]

    order = models.ForeignKey(
        "scanner.Order", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="auxiliary_material_lots", verbose_name="Проект / заказ",
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, db_index=True, verbose_name="Категория")
    name = models.CharField(max_length=500, verbose_name="Наименование")
    brand = models.CharField(max_length=220, blank=True, verbose_name="Марка / производитель")
    characteristics = models.CharField(max_length=500, blank=True, verbose_name="Характеристика")
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES, verbose_name="Единица учёта")
    quantity_initial = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Принято")
    quantity_remaining = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Остаток")
    package_description = models.CharField(max_length=220, blank=True, verbose_name="Тара / упаковка")
    density_kg_l = models.DecimalField(max_digits=10, decimal_places=5, null=True, blank=True, verbose_name="Плотность, кг/л")
    thickness_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, verbose_name="Толщина, мм")
    width_mm = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True, verbose_name="Ширина, мм")
    length_mm = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True, verbose_name="Длина, мм")
    mesh_cell_width_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, verbose_name="Ячейка по ширине, мм")
    mesh_cell_height_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, verbose_name="Ячейка по высоте, мм")
    wire_diameter_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, verbose_name="Диаметр проволоки, мм")
    batch_number = models.CharField(max_length=160, blank=True, verbose_name="Партия")
    expiry_date = models.DateField(null=True, blank=True, verbose_name="Срок годности")
    storage_location = models.CharField(max_length=180, blank=True, verbose_name="Место хранения")
    hazardous = models.BooleanField(default=False, verbose_name="Опасный материал / ЛВЖ")
    minimum_stock = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Минимальный остаток")
    received_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата прихода")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Принял")

    class Meta:
        ordering = ["category", "name", "brand", "received_at"]
        verbose_name = "Прочий материал на складе"
        verbose_name_plural = "Прочие материалы на складе"

    @property
    def is_low_stock(self):
        return self.minimum_stock > 0 and self.quantity_remaining <= self.minimum_stock

    @property
    def estimated_mass_kg(self):
        if self.unit == "kg":
            return self.quantity_remaining
        if self.unit == "g":
            return (self.quantity_remaining / Decimal("1000")).quantize(Decimal("0.001"))
        if self.unit == "l" and self.density_kg_l:
            return (self.quantity_remaining * self.density_kg_l).quantize(Decimal("0.001"))
        if self.unit == "ml" and self.density_kg_l:
            return (self.quantity_remaining * self.density_kg_l / Decimal("1000")).quantize(Decimal("0.001"))
        return None

    @property
    def dimensions_display(self):
        values = []
        if self.thickness_mm:
            values.append(f"толщина {self.thickness_mm} мм")
        if self.width_mm and self.length_mm:
            values.append(f"{self.width_mm} × {self.length_mm} мм")
        elif self.width_mm:
            values.append(f"ширина {self.width_mm} мм")
        if self.mesh_cell_width_mm and self.mesh_cell_height_mm:
            values.append(f"ячейка {self.mesh_cell_width_mm} × {self.mesh_cell_height_mm} мм")
        if self.wire_diameter_mm:
            values.append(f"проволока Ø{self.wire_diameter_mm} мм")
        return "; ".join(values)

    def __str__(self):
        return f"{self.name}: {self.quantity_remaining} {self.get_unit_display()}"


class AuxiliaryMaterialRequirement(models.Model):
    order = models.ForeignKey(
        "scanner.Order", on_delete=models.CASCADE,
        related_name="auxiliary_material_requirements", verbose_name="Проект / заказ",
    )
    assembly_name = models.CharField(max_length=500, blank=True, verbose_name="Узел / подсборка")
    category = models.CharField(max_length=20, choices=AuxiliaryMaterialLot.CATEGORY_CHOICES, db_index=True, verbose_name="Категория")
    name = models.CharField(max_length=500, verbose_name="Наименование")
    brand = models.CharField(max_length=220, blank=True, verbose_name="Марка / производитель")
    characteristics = models.CharField(max_length=500, blank=True, verbose_name="Характеристика")
    unit = models.CharField(max_length=10, choices=AuxiliaryMaterialLot.UNIT_CHOICES, verbose_name="Единица учёта")
    quantity_required = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Требуется")
    package_description = models.CharField(max_length=220, blank=True, verbose_name="Тара / упаковка")
    density_kg_l = models.DecimalField(max_digits=10, decimal_places=5, null=True, blank=True, verbose_name="Плотность, кг/л")
    thickness_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, verbose_name="Толщина, мм")
    width_mm = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True, verbose_name="Ширина, мм")
    length_mm = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True, verbose_name="Длина, мм")
    mesh_cell_width_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, verbose_name="Ячейка по ширине, мм")
    mesh_cell_height_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, verbose_name="Ячейка по высоте, мм")
    wire_diameter_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, verbose_name="Диаметр проволоки, мм")
    notes = models.TextField(blank=True, verbose_name="Примечание")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "assembly_name", "category", "name"]
        verbose_name = "Потребность в прочем материале"
        verbose_name_plural = "Потребности в прочих материалах"

    @property
    def dimensions_display(self):
        probe = AuxiliaryMaterialLot(
            thickness_mm=self.thickness_mm, width_mm=self.width_mm, length_mm=self.length_mm,
            mesh_cell_width_mm=self.mesh_cell_width_mm, mesh_cell_height_mm=self.mesh_cell_height_mm,
            wire_diameter_mm=self.wire_diameter_mm,
        )
        return probe.dimensions_display

    def __str__(self):
        return f"{self.order}: {self.name}"


class AuxiliaryMaterialTransaction(models.Model):
    TRANSACTION_TYPES = [("in", "Приход"), ("out", "Выдача"), ("adjustment", "Корректировка")]

    stock_lot = models.ForeignKey(AuxiliaryMaterialLot, on_delete=models.PROTECT, related_name="transactions", verbose_name="Материал")
    transaction_type = models.CharField(max_length=12, choices=TRANSACTION_TYPES, verbose_name="Тип")
    quantity = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name="Количество")
    recipient = models.ForeignKey("scanner.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="auxiliary_material_receipts", verbose_name="Получатель")
    recipient_name = models.CharField(max_length=255, blank=True, verbose_name="Получатель (на момент выдачи)")
    basis = models.CharField(max_length=300, blank=True, verbose_name="Основание")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Выдал / принял")

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Движение прочего материала"
        verbose_name_plural = "Движения прочих материалов"

    def __str__(self):
        return f"{self.get_transaction_type_display()} — {self.stock_lot.name}"
