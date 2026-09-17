from collections import OrderedDict
from decimal import Decimal
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from scanner.material_models import (
    AuxiliaryMaterialLot,
    AuxiliaryMaterialTransaction,
    MaterialGrade,
    MaterialRequirement,
    MaterialStockLot,
    MaterialTransaction,
    ZERO,
)
from scanner.material_services import decimal_value, validate_material_geometry
from scanner.models import Order


SHEET_PROFILE_DEFAULTS = {
    "лист": "sheet",
    "труба": "round_pipe",
    "труба круглая": "round_pipe",
    "труба профильная": "rect_tube",
    "прокат": None,
    "остатки": None,
}

PROFILE_ALIASES = {
    "лист": "sheet",
    "sheet": "sheet",
    "труба": "round_pipe",
    "труба круглая": "round_pipe",
    "круглая труба": "round_pipe",
    "round_pipe": "round_pipe",
    "труба профильная": "rect_tube",
    "профильная труба": "rect_tube",
    "rect_tube": "rect_tube",
    "круг": "round_bar",
    "round_bar": "round_bar",
    "квадрат": "square_bar",
    "square_bar": "square_bar",
    "шестигранник": "hex_bar",
    "шестигранный прокат": "hex_bar",
    "hex_bar": "hex_bar",
    "полоса": "rect_bar",
    "прямоугольник": "rect_bar",
    "rect_bar": "rect_bar",
    "уголок": "angle",
    "angle": "angle",
    "швеллер": "channel",
    "channel": "channel",
    "балка": "beam",
    "двутавр": "beam",
    "двутавровая балка": "beam",
    "beam": "beam",
    "полособульб": "bulb_flat",
    "полособульбовый профиль": "bulb_flat",
    "bulb_flat": "bulb_flat",
    "другой": "other",
    "прочее": "other",
    "other": "other",
}

FIELD_ALIASES = {
    "name": ["наименование", "название"],
    "grade": ["марка материала", "марка"],
    "profile_type": ["вид профиля", "тип профиля", "вид трубы", "профиль"],
    "profile_name": ["сортамент", "обозначение профиля", "обозначение"],
    "quantity": ["количество", "кол-во", "шт"],
    "piece_length_mm": ["длина куска", "длина листа", "длина единицы", "длина, мм", "длина"],
    "total_length_mm": ["общая длина", "всего длина"],
    "piece_length_m": ["длина куска, м", "длина листа, м", "длина единицы, м", "длина, м"],
    "total_length_m": ["общая длина, м", "всего длина, м"],
    "thickness_mm": ["толщина листа", "толщина, мм", "толщина"],
    "width_mm": ["ширина, мм", "ширина"],
    "height_mm": ["высота, мм", "высота"],
    "outer_diameter_mm": ["наружный диаметр", "диаметр, мм", "диаметр"],
    "wall_thickness_mm": ["толщина стенки", "стенка, мм", "стенка"],
    "kg_per_meter": ["масса 1 м", "кг/м"],
    "unit_mass_kg": ["масса 1 шт", "кг/шт"],
    "batch_number": ["партия / плавка", "номер партии", "плавка", "партия"],
    "storage_location": ["место хранения", "место", "ячейка"],
    "order_number": ["номер проекта", "проект", "заказ"],
    "basis": ["основание прихода", "основание"],
    "assembly_name": ["узел / подсборка", "подсборка", "узел"],
    "category": ["категория", "вид материала"],
    "brand": ["марка / производитель", "производитель", "марка"],
    "characteristics": ["характеристика", "характеристики"],
    "unit": ["единица учета", "единица", "ед. изм."],
    "package_description": ["тара / упаковка", "тара", "упаковка"],
    "density_kg_l": ["плотность, кг/л", "плотность кг/л", "кг/л"],
    "mesh_cell_width_mm": ["ячейка ширина", "ячейка x", "ячейка х"],
    "mesh_cell_height_mm": ["ячейка высота", "ячейка y", "ячейка у"],
    "wire_diameter_mm": ["диаметр проволоки", "проволока"],
    "expiry_date": ["срок годности", "годен до"],
    "hazardous": ["лвж / опасный", "опасный", "лвж"],
    "minimum_stock": ["минимальный остаток", "минимум"],
}

AUXILIARY_CATEGORY_ALIASES = {
    "краска": "paint", "покрытие": "paint", "краска / покрытие": "paint", "paint": "paint",
    "растворитель": "solvent", "solvent": "solvent",
    "смазка": "lubricant", "масло": "lubricant", "смазка / масло": "lubricant", "lubricant": "lubricant",
    "резина": "rubber", "прокладочный материал": "rubber", "резина / прокладочный материал": "rubber", "rubber": "rubber",
    "сетка": "mesh", "mesh": "mesh",
    "сыпучий": "bulk", "сыпучий материал": "bulk", "bulk": "bulk",
    "штучный": "piece", "штучный расходный материал": "piece", "piece": "piece",
    "прочее": "other", "other": "other",
}

AUXILIARY_UNIT_ALIASES = {
    "кг": "kg", "kg": "kg",
    "г": "g", "гр": "g", "g": "g",
    "л": "l", "литр": "l", "литры": "l", "l": "l",
    "мл": "ml", "ml": "ml",
    "шт": "pcs", "шт.": "pcs", "pcs": "pcs",
    "м": "m", "пог. м": "m", "m": "m",
    "м2": "m2", "м²": "m2", "m2": "m2",
    "рулон": "roll", "roll": "roll",
    "упаковка": "pack", "уп": "pack", "pack": "pack",
}


def _normalise_header(value):
    return " ".join(str(value or "").strip().casefold().replace("ё", "е").split())


def _row_value(row, headers, field, default=None):
    aliases = [_normalise_header(value) for value in FIELD_ALIASES[field]]
    for alias in aliases:
        for index, header in enumerate(headers):
            if header == alias or header.startswith(f"{alias},"):
                return row[index] if index < len(row) else default
    return default


def _text(value):
    return str(value or "").strip()


def _serial_decimal(value):
    return str(value) if value is not None else None


def _length_mm(row, headers, meter_field="piece_length_m", millimeter_field="piece_length_mm", default=None):
    meters = decimal_value(_row_value(row, headers, meter_field, None), None)
    if meters is not None:
        return meters * Decimal("1000")
    return decimal_value(_row_value(row, headers, millimeter_field, default), default)


def _date_value(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    for pattern in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    raise ValidationError(f"некорректный срок годности «{text}»")


def _bool_value(value):
    return _normalise_header(value) in {"1", "да", "yes", "true", "+", "лвж"}


def _order_value(row, headers):
    order_number = _text(_row_value(row, headers, "order_number", ""))
    if not order_number:
        return None
    order = Order.objects.filter(order_number__iexact=order_number).first()
    if not order:
        raise ValidationError(f"проект «{order_number}» не найден")
    return order


def _parse_auxiliary_row(sheet, row_number, row, headers):
    name = _text(_row_value(row, headers, "name", ""))
    category_raw = _text(_row_value(row, headers, "category", ""))
    unit_raw = _text(_row_value(row, headers, "unit", ""))
    category = AUXILIARY_CATEGORY_ALIASES.get(_normalise_header(category_raw))
    unit = AUXILIARY_UNIT_ALIASES.get(_normalise_header(unit_raw))
    if not name or not category_raw or not unit_raw:
        raise ValidationError("обязательны Наименование, Категория и Единица учёта")
    if not category:
        raise ValidationError(f"неизвестная категория «{category_raw}»")
    if not unit:
        raise ValidationError(f"неизвестная единица «{unit_raw}»")
    quantity = decimal_value(_row_value(row, headers, "quantity", 0))
    if quantity <= 0:
        raise ValidationError("количество должно быть больше нуля")
    density = decimal_value(_row_value(row, headers, "density_kg_l", None), None)
    width = decimal_value(_row_value(row, headers, "width_mm", None), None)
    length = _length_mm(row, headers)
    estimated_mass = ZERO
    if unit == "kg":
        estimated_mass = quantity
    elif unit == "g":
        estimated_mass = quantity / Decimal("1000")
    elif unit == "l" and density:
        estimated_mass = quantity * density
    elif unit == "ml" and density:
        estimated_mass = quantity * density / Decimal("1000")
    estimated_area = quantity if unit == "m2" else ZERO
    if estimated_area == ZERO and unit == "pcs" and width and length:
        estimated_area = width * length * quantity / Decimal("1000000")
    order = _order_value(row, headers)
    return {
        "record_type": "auxiliary",
        "sheet": sheet.title,
        "row": row_number,
        "name": name,
        "assembly_name": _text(_row_value(row, headers, "assembly_name", "")),
        "category": category,
        "category_label": dict(AuxiliaryMaterialLot.CATEGORY_CHOICES)[category],
        "brand": _text(_row_value(row, headers, "brand", "")),
        "characteristics": _text(_row_value(row, headers, "characteristics", "")),
        "unit": unit,
        "unit_label": dict(AuxiliaryMaterialLot.UNIT_CHOICES)[unit],
        "quantity": _serial_decimal(quantity),
        "package_description": _text(_row_value(row, headers, "package_description", "")),
        "density_kg_l": _serial_decimal(density),
        "thickness_mm": _serial_decimal(decimal_value(_row_value(row, headers, "thickness_mm", None), None)),
        "width_mm": _serial_decimal(width),
        "length_mm": _serial_decimal(length),
        "length_m": _serial_decimal(length / Decimal("1000") if length is not None else None),
        "mesh_cell_width_mm": _serial_decimal(decimal_value(_row_value(row, headers, "mesh_cell_width_mm", None), None)),
        "mesh_cell_height_mm": _serial_decimal(decimal_value(_row_value(row, headers, "mesh_cell_height_mm", None), None)),
        "wire_diameter_mm": _serial_decimal(decimal_value(_row_value(row, headers, "wire_diameter_mm", None), None)),
        "batch_number": _text(_row_value(row, headers, "batch_number", "")),
        "expiry_date": _date_value(_row_value(row, headers, "expiry_date", None)),
        "storage_location": _text(_row_value(row, headers, "storage_location", "")),
        "hazardous": _bool_value(_row_value(row, headers, "hazardous", "")),
        "minimum_stock": _serial_decimal(decimal_value(_row_value(row, headers, "minimum_stock", 0))),
        "basis": _text(_row_value(row, headers, "basis", "")) or "Загрузка прочих материалов из Excel",
        "order_id": order.id if order else None,
        "order_number": order.order_number if order else "Общий склад",
        "mass_kg": _serial_decimal(estimated_mass.quantize(Decimal("0.001"))),
        "area_m2": _serial_decimal(estimated_area.quantize(Decimal("0.001"))),
        "lots_to_create": 1,
    }


def _profile_for_sheet(sheet, row, headers):
    sheet_name = _normalise_header(sheet.title)
    default = SHEET_PROFILE_DEFAULTS.get(sheet_name)
    raw = _text(_row_value(row, headers, "profile_type", ""))
    if raw:
        return PROFILE_ALIASES.get(_normalise_header(raw))
    return default


def parse_stock_workbook(workbook):
    """Validate a stock workbook without changing warehouse balances."""
    prepared = []
    errors = []
    ignored_sheets = {"справочник плотностей", "инструкция"}

    for sheet in workbook.worksheets:
        sheet_key = _normalise_header(sheet.title)
        if sheet_key in ignored_sheets:
            continue
        headers = [_normalise_header(cell.value) for cell in sheet[1]]
        if not any(headers):
            continue
        for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            if not row or all(value in (None, "") for value in row):
                continue
            try:
                if sheet_key == "прочие материалы":
                    prepared.append(_parse_auxiliary_row(sheet, row_number, row, headers))
                    continue
                name = _text(_row_value(row, headers, "name", ""))
                grade_name = _text(_row_value(row, headers, "grade", ""))
                profile_type = _profile_for_sheet(sheet, row, headers)
                if not name or not grade_name:
                    raise ValidationError("обязательны Наименование и Марка материала")
                if not profile_type:
                    raw_profile = _text(_row_value(row, headers, "profile_type", "")) or "не указан"
                    raise ValidationError(f"неизвестный вид профиля «{raw_profile}»")

                grade = MaterialGrade.objects.filter(name__iexact=grade_name, is_active=True).first()
                if not grade:
                    raise ValidationError(f"марка «{grade_name}» отсутствует в справочнике плотностей")

                quantity = decimal_value(_row_value(row, headers, "quantity", 0))
                piece_length = _length_mm(row, headers)
                total_length = _length_mm(
                    row, headers, meter_field="total_length_m",
                    millimeter_field="total_length_mm", default=ZERO,
                )
                if quantity <= 0:
                    raise ValidationError("количество должно быть больше нуля")
                if quantity != quantity.to_integral_value():
                    raise ValidationError("количество листов или кусков должно быть целым")
                if profile_type != "sheet" and (piece_length is None or piece_length <= 0):
                    raise ValidationError("для трубы и проката укажите длину каждого куска")
                calculated_length = piece_length * quantity if piece_length else ZERO
                if profile_type != "sheet":
                    if total_length > 0 and abs(total_length - calculated_length) > Decimal("0.001"):
                        raise ValidationError(
                            f"общая длина {total_length / Decimal('1000')} м не равна длине куска × количеству ({calculated_length / Decimal('1000')} м)"
                        )
                    total_length = calculated_length

                order = _order_value(row, headers)

                values = {
                    "name": name,
                    "grade": grade,
                    "profile_type": profile_type,
                    "profile_name": _text(_row_value(row, headers, "profile_name", "")),
                    "quantity_initial": quantity,
                    "length_initial_mm": total_length,
                    "piece_length_mm": piece_length,
                    "thickness_mm": decimal_value(_row_value(row, headers, "thickness_mm", None), None),
                    "width_mm": decimal_value(_row_value(row, headers, "width_mm", None), None),
                    "height_mm": decimal_value(_row_value(row, headers, "height_mm", None), None),
                    "outer_diameter_mm": decimal_value(_row_value(row, headers, "outer_diameter_mm", None), None),
                    "wall_thickness_mm": decimal_value(_row_value(row, headers, "wall_thickness_mm", None), None),
                    "kg_per_meter": decimal_value(_row_value(row, headers, "kg_per_meter", None), None),
                    "unit_mass_kg": decimal_value(_row_value(row, headers, "unit_mass_kg", None), None),
                    "batch_number": _text(_row_value(row, headers, "batch_number", "")),
                    "storage_location": _text(_row_value(row, headers, "storage_location", "")),
                    "basis": _text(_row_value(row, headers, "basis", "")) or "Загрузка начальных остатков из Excel",
                    "order": order,
                }
                probe = MaterialStockLot(**{key: value for key, value in values.items() if key != "basis"})
                validate_material_geometry(probe)
                mass = probe.calculate_mass(quantity, total_length)
                area = probe.calculate_area_m2(quantity)
                prepared.append({
                    "record_type": "metal",
                    "sheet": sheet.title,
                    "row": row_number,
                    "name": name,
                    "assembly_name": _text(_row_value(row, headers, "assembly_name", "")),
                    "grade_id": grade.id,
                    "grade_name": grade.name,
                    "profile_type": profile_type,
                    "profile_label": dict(MaterialRequirement.PROFILE_CHOICES)[profile_type],
                    "profile_name": values["profile_name"],
                    "quantity": _serial_decimal(quantity),
                    "piece_length_mm": _serial_decimal(piece_length),
                    "piece_length_m": _serial_decimal(piece_length / Decimal("1000") if piece_length is not None else None),
                    "total_length_mm": _serial_decimal(total_length),
                    "total_length_m": _serial_decimal(total_length / Decimal("1000")),
                    "thickness_mm": _serial_decimal(values["thickness_mm"]),
                    "width_mm": _serial_decimal(values["width_mm"]),
                    "height_mm": _serial_decimal(values["height_mm"]),
                    "outer_diameter_mm": _serial_decimal(values["outer_diameter_mm"]),
                    "wall_thickness_mm": _serial_decimal(values["wall_thickness_mm"]),
                    "kg_per_meter": _serial_decimal(values["kg_per_meter"]),
                    "unit_mass_kg": _serial_decimal(values["unit_mass_kg"]),
                    "batch_number": values["batch_number"],
                    "storage_location": values["storage_location"],
                    "basis": values["basis"],
                    "order_id": order.id if order else None,
                    "order_number": order.order_number if order else "Общий склад",
                    "mass_kg": _serial_decimal(mass),
                    "area_m2": _serial_decimal(area),
                    "lots_to_create": int(quantity) if profile_type != "sheet" and quantity == quantity.to_integral_value() else 1,
                })
            except ValidationError as exc:
                errors.append(f"{sheet.title}, строка {row_number}: {'; '.join(exc.messages)}")
            except Exception as exc:
                errors.append(f"{sheet.title}, строка {row_number}: {exc}")
    if not prepared and not errors:
        errors.append("В файле не найдено строк с остатками.")
    return prepared, errors


@transaction.atomic
def create_stock_lots_from_preview(rows, user, transaction_type="in"):
    """Create warehouse receipts; linear rows are split into traceable pieces."""
    created_lots = []
    for row in rows:
        if row.get("record_type") == "auxiliary":
            lot = AuxiliaryMaterialLot.objects.create(
                order_id=row.get("order_id") or None,
                category=row["category"],
                name=row["name"],
                brand=row.get("brand", ""),
                characteristics=row.get("characteristics", ""),
                unit=row["unit"],
                quantity_initial=decimal_value(row["quantity"]),
                quantity_remaining=decimal_value(row["quantity"]),
                package_description=row.get("package_description", ""),
                density_kg_l=decimal_value(row.get("density_kg_l"), None),
                thickness_mm=decimal_value(row.get("thickness_mm"), None),
                width_mm=decimal_value(row.get("width_mm"), None),
                length_mm=decimal_value(row.get("length_mm"), None),
                mesh_cell_width_mm=decimal_value(row.get("mesh_cell_width_mm"), None),
                mesh_cell_height_mm=decimal_value(row.get("mesh_cell_height_mm"), None),
                wire_diameter_mm=decimal_value(row.get("wire_diameter_mm"), None),
                batch_number=row.get("batch_number", ""),
                expiry_date=row.get("expiry_date") or None,
                storage_location=row.get("storage_location", ""),
                hazardous=bool(row.get("hazardous")),
                minimum_stock=decimal_value(row.get("minimum_stock")),
                created_by=user,
            )
            AuxiliaryMaterialTransaction.objects.create(
                stock_lot=lot,
                transaction_type=transaction_type,
                quantity=lot.quantity_initial,
                basis=row.get("basis") or "Загрузка прочих материалов из Excel",
                created_by=user,
            )
            created_lots.append(lot)
            continue
        grade = MaterialGrade.objects.get(pk=row["grade_id"], is_active=True)
        quantity = decimal_value(row["quantity"])
        piece_length = decimal_value(row.get("piece_length_mm"), None)
        is_linear = row["profile_type"] in MaterialStockLot.LINEAR_PROFILES
        if is_linear and quantity != quantity.to_integral_value():
            raise ValidationError(f'{row["name"]}: количество кусков должно быть целым.')
        parts = int(quantity) if is_linear else 1
        for _ in range(parts):
            lot_quantity = Decimal("1") if is_linear else quantity
            lot_length = piece_length if is_linear else ZERO
            lot = MaterialStockLot(
                order_id=row.get("order_id") or None,
                grade=grade,
                name=row["name"],
                profile_type=row["profile_type"],
                profile_name=row.get("profile_name", ""),
                batch_number=row.get("batch_number", ""),
                storage_location=row.get("storage_location", ""),
                quantity_initial=lot_quantity,
                length_initial_mm=lot_length,
                piece_length_mm=piece_length,
                thickness_mm=decimal_value(row.get("thickness_mm"), None),
                width_mm=decimal_value(row.get("width_mm"), None),
                height_mm=decimal_value(row.get("height_mm"), None),
                outer_diameter_mm=decimal_value(row.get("outer_diameter_mm"), None),
                wall_thickness_mm=decimal_value(row.get("wall_thickness_mm"), None),
                kg_per_meter=decimal_value(row.get("kg_per_meter"), None),
                unit_mass_kg=decimal_value(row.get("unit_mass_kg"), None),
                created_by=user,
            )
            validate_material_geometry(lot)
            lot.initialize_balances()
            lot.save()
            MaterialTransaction.objects.create(
                stock_lot=lot,
                order=lot.order,
                transaction_type=transaction_type,
                quantity=lot.quantity_initial,
                length_mm=lot.length_initial_mm,
                mass_kg=lot.mass_initial_kg,
                basis=row.get("basis") or "Загрузка начальных остатков из Excel",
                created_by=user,
            )
            created_lots.append(lot)
    return created_lots


@transaction.atomic
def reconcile_stock_from_preview(rows, user):
    """Replace active balances in one scope with an inventory snapshot, preserving history."""
    if not rows:
        raise ValidationError("В инвентаризации нет складских позиций.")
    order_ids = {row.get("order_id") or None for row in rows}
    if len(order_ids) != 1:
        raise ValidationError("Одна инвентаризация должна относиться к одному складу или проекту.")
    order_id = order_ids.pop()
    inventory_all_scopes = bool(rows[0].get("inventory_all_scopes"))
    inventory_sections = rows[0].get("inventory_sections") or {}
    has_metal = inventory_sections.get(
        "metal", any(row.get("record_type") != "auxiliary" for row in rows)
    )
    has_auxiliary = inventory_sections.get(
        "auxiliary", any(row.get("record_type") == "auxiliary" for row in rows)
    )
    basis = rows[0].get("basis") or "Остатки после инвентаризации"
    adjusted = 0

    if has_metal:
        metal_lots = MaterialStockLot.objects.select_for_update()
        if not inventory_all_scopes:
            metal_lots = metal_lots.filter(order_id=order_id)
        for lot in metal_lots:
            if lot.quantity_remaining == ZERO and lot.length_remaining_mm == ZERO and lot.mass_remaining_kg == ZERO:
                continue
            MaterialTransaction.objects.create(
                stock_lot=lot,
                order=lot.order,
                transaction_type="adjustment",
                quantity=-lot.quantity_remaining,
                length_mm=-lot.length_remaining_mm,
                mass_kg=-lot.mass_remaining_kg,
                basis=f"{basis}: замена предыдущего остатка",
                created_by=user,
            )
            lot.quantity_remaining = ZERO
            lot.length_remaining_mm = ZERO
            lot.mass_remaining_kg = ZERO
            lot.save(update_fields=["quantity_remaining", "length_remaining_mm", "mass_remaining_kg"])
            adjusted += 1

    if has_auxiliary:
        auxiliary_lots = AuxiliaryMaterialLot.objects.select_for_update()
        if not inventory_all_scopes:
            auxiliary_lots = auxiliary_lots.filter(order_id=order_id)
        for lot in auxiliary_lots:
            if lot.quantity_remaining == ZERO:
                continue
            AuxiliaryMaterialTransaction.objects.create(
                stock_lot=lot,
                transaction_type="adjustment",
                quantity=-lot.quantity_remaining,
                basis=f"{basis}: замена предыдущего остатка",
                created_by=user,
            )
            lot.quantity_remaining = ZERO
            lot.save(update_fields=["quantity_remaining"])
            adjusted += 1

    created = create_stock_lots_from_preview(rows, user, transaction_type="adjustment")
    return created, adjusted


def group_auxiliary_lots(lots):
    groups = OrderedDict()
    for lot in lots:
        key = (
            lot.category,
            lot.name.strip().casefold(),
            lot.brand.strip().casefold(),
            lot.characteristics.strip().casefold(),
            lot.unit,
            str(lot.thickness_mm or ""),
            str(lot.width_mm or ""),
            str(lot.length_mm or ""),
            str(lot.mesh_cell_width_mm or ""),
            str(lot.mesh_cell_height_mm or ""),
            str(lot.wire_diameter_mm or ""),
        )
        if key not in groups:
            groups[key] = {
                "category": lot.category,
                "category_label": lot.get_category_display(),
                "name": lot.name,
                "brand": lot.brand,
                "characteristics": lot.characteristics,
                "unit": lot.unit,
                "unit_label": lot.get_unit_display(),
                "dimensions": lot.dimensions_display,
                "quantity": ZERO,
                "mass_kg": ZERO,
                "has_mass": False,
                "lots": [],
            }
        group = groups[key]
        group["quantity"] += lot.quantity_remaining
        if lot.estimated_mass_kg is not None:
            group["mass_kg"] += lot.estimated_mass_kg
            group["has_mass"] = True
        group["lots"].append(lot)
    return list(groups.values())


def stock_group_key(lot):
    def number(field):
        value = getattr(lot, field)
        return str(value.normalize()) if value is not None else ""

    dimensions = [
        number("thickness_mm"),
        number("width_mm"),
        number("height_mm"),
        number("outer_diameter_mm"),
        number("wall_thickness_mm"),
        number("kg_per_meter"),
        number("unit_mass_kg"),
    ]
    if lot.profile_type == "sheet":
        dimensions.append(number("piece_length_mm"))
    return (
        lot.grade_id,
        lot.profile_type,
        (
            (lot.profile_name or "").strip().casefold()
            if lot.profile_type in {"angle", "channel", "beam", "bulb_flat", "other"}
            else ""
        ),
        *dimensions,
    )


def stock_group_dimensions(lot):
    if lot.profile_type == "sheet":
        length_m = lot.piece_length_mm / Decimal("1000") if lot.piece_length_mm else "—"
        return f"{lot.thickness_mm or '—'} мм × {lot.width_mm or '—'} мм × {length_m} м"
    if lot.profile_type == "round_pipe":
        return f"Ø{lot.outer_diameter_mm or '—'} × {lot.wall_thickness_mm or '—'} мм"
    if lot.profile_type == "rect_tube":
        return f"{lot.width_mm or '—'} × {lot.height_mm or '—'} × {lot.wall_thickness_mm or '—'} мм"
    if lot.profile_type == "round_bar":
        return f"Ø{lot.outer_diameter_mm or '—'} мм"
    if lot.profile_type == "hex_bar":
        return f"S{lot.width_mm or '—'} мм"
    if lot.profile_type == "square_bar":
        return f"{lot.width_mm or '—'} × {lot.width_mm or '—'} мм"
    if lot.profile_type == "rect_bar":
        return f"{lot.width_mm or '—'} × {lot.height_mm or lot.thickness_mm or '—'} мм"
    return lot.profile_name or "Размер по сортаменту"


def group_stock_lots(lots):
    groups = OrderedDict()
    for lot in lots:
        key = stock_group_key(lot)
        if key not in groups:
            groups[key] = {
                "name": lot.profile_name or lot.name,
                "grade": lot.grade,
                "profile_type": lot.profile_type,
                "profile_label": lot.get_profile_type_display(),
                "dimensions": stock_group_dimensions(lot),
                "quantity": ZERO,
                "length_mm": ZERO,
                "mass_kg": ZERO,
                "area_m2": ZERO,
                "lots": [],
            }
        group = groups[key]
        group["quantity"] += lot.quantity_remaining
        group["length_mm"] += lot.length_remaining_mm
        group["mass_kg"] += lot.mass_remaining_kg
        if lot.profile_type == "sheet":
            group["area_m2"] += lot.calculate_area_m2(lot.quantity_remaining)
        group["lots"].append(lot)

    for group in groups.values():
        detail_groups = OrderedDict()
        for lot in group["lots"]:
            if lot.is_linear:
                pieces = lot.quantity_remaining if lot.quantity_remaining > 0 else Decimal("1")
                length_each = (
                    lot.length_remaining_mm / pieces if pieces > 0 else lot.length_remaining_mm
                ).quantize(Decimal("0.001"))
            else:
                pieces = lot.quantity_remaining
                length_each = lot.piece_length_mm or ZERO
            key = (
                str(length_each),
                lot.order_id,
                lot.storage_location,
            )
            if key not in detail_groups:
                detail_groups[key] = {
                    "length_each_mm": length_each,
                    "quantity": ZERO,
                    "total_length_mm": ZERO,
                    "mass_kg": ZERO,
                    "order": lot.order,
                    "storage_location": lot.storage_location,
                }
            detail = detail_groups[key]
            detail["quantity"] += pieces
            detail["total_length_mm"] += lot.length_remaining_mm
            detail["mass_kg"] += lot.mass_remaining_kg
        group["length_m"] = (group["length_mm"] / Decimal("1000")).quantize(Decimal("0.001"))
        group["details"] = list(detail_groups.values())
        for detail in group["details"]:
            detail["length_each_m"] = (detail["length_each_mm"] / Decimal("1000")).quantize(Decimal("0.001"))
            detail["total_length_m"] = (detail["total_length_mm"] / Decimal("1000")).quantize(Decimal("0.001"))
    return list(groups.values())
