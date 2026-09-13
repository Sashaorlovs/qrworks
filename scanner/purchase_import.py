from collections import OrderedDict, defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction

from scanner.models import Order, OrderItem
from scanner.purchase_models import PurchaseItem
from scanner.purchase_normalization import clean_purchase_name, normalize_purchase_name


def _headers(sheet):
    return [" ".join(str(cell.value or "").strip().casefold().replace("ё", "е").split()) for cell in sheet[1]]


def _index(headers, aliases):
    for alias in aliases:
        for index, header in enumerate(headers):
            if alias in header:
                return index
    return None


def _integer(value, label):
    if value in (None, ""):
        return 0
    try:
        number = float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        raise ValidationError(f"{label}: некорректное число «{value}»")
    if number < 0 or not number.is_integer():
        raise ValidationError(f"{label}: укажите целое неотрицательное количество")
    return int(number)


def parse_purchase_workbook(workbook, order):
    spec_sheet = purchase_sheet = None
    spec_headers = purchase_headers = None
    ignored = {"инструкция", "пример", "readme"}
    for sheet in workbook.worksheets:
        if sheet.title.strip().casefold() in ignored:
            continue
        headers = _headers(sheet)
        has_name = _index(headers, ["наименование", "название"]) is not None
        has_required = _index(headers, ["требуемое", "потребность", "кол-во", "количество"]) is not None
        has_assembly = _index(headers, ["подсборка", "сборка", "узел"]) is not None
        has_purchased = _index(headers, ["закуплено", "закупленное", "закуп", "purchased"]) is not None
        if has_name and (has_assembly or has_required) and sheet.title.strip().casefold() not in {"закупка", "приход"}:
            spec_sheet, spec_headers = sheet, headers
        elif has_name and has_purchased:
            purchase_sheet, purchase_headers = sheet, headers

    errors, warnings = [], []
    if spec_sheet is None:
        return [], ["Не найден лист спецификации со столбцами «Наименование» и «Требуемое количество»"], []

    purchased = defaultdict(int)
    purchased_variants = defaultdict(set)
    if purchase_sheet is not None:
        name_index = _index(purchase_headers, ["наименование", "название"])
        quantity_index = _index(purchase_headers, ["закуплено", "закупленное", "закуп", "purchased"])
        for row_number, row in enumerate(purchase_sheet.iter_rows(min_row=2, values_only=True), start=2):
            if not row or all(value in (None, "") for value in row):
                continue
            try:
                name = clean_purchase_name(row[name_index] if name_index is not None and name_index < len(row) else "")
                if not name:
                    raise ValidationError("не заполнено наименование")
                key = normalize_purchase_name(name)
                purchased[key] += _integer(row[quantity_index] if quantity_index is not None and quantity_index < len(row) else 0, "Закуплено")
                purchased_variants[key].add(str(row[name_index]).strip())
            except ValidationError as exc:
                errors.append(f"{purchase_sheet.title}, строка {row_number}: {'; '.join(exc.messages)}")

    name_index = _index(spec_headers, ["наименование", "название"])
    required_index = _index(spec_headers, ["требуемое", "потребность", "кол-во", "количество"])
    assembly_index = _index(spec_headers, ["подсборка", "сборка", "узел"])
    designation_index = _index(spec_headers, ["обозначение", "артикул"])
    purchased_index = _index(spec_headers, ["закуплено", "закупленное", "закуп", "purchased"])
    if required_index is None:
        return [], ["На листе спецификации не найден столбец «Требуемое количество»"], []

    grouped = OrderedDict()
    variants = defaultdict(set)
    for row_number, row in enumerate(spec_sheet.iter_rows(min_row=2, values_only=True), start=2):
        if not row or all(value in (None, "") for value in row):
            continue
        try:
            raw_name = str(row[name_index] or "").strip() if name_index is not None and name_index < len(row) else ""
            name = clean_purchase_name(raw_name)
            if not name:
                raise ValidationError("не заполнено наименование")
            required = _integer(row[required_index] if required_index < len(row) else 0, "Требуется")
            if required <= 0:
                raise ValidationError("требуемое количество должно быть больше нуля")
            assembly = " ".join(str(row[assembly_index] or "").strip().split()) if assembly_index is not None and assembly_index < len(row) else ""
            designation = " ".join(str(row[designation_index] or "").strip().split()) if designation_index is not None and designation_index < len(row) else ""
            normalized = normalize_purchase_name(name)
            key = (normalized, assembly.casefold())
            variants[normalized].add(raw_name)
            if key not in grouped:
                direct_purchased = _integer(row[purchased_index] if purchased_index is not None and purchased_index < len(row) else 0, "Закуплено")
                total_purchased = purchased.get(normalized, direct_purchased if purchased_index is not None else required)
                grouped[key] = {
                    "item_name": name, "normalized_name": normalized, "designation": designation,
                    "assembly_name": assembly, "quantity_required": 0,
                    "quantity_purchased": total_purchased, "source_rows": [],
                }
            grouped[key]["quantity_required"] += required
            grouped[key]["source_rows"].append(row_number)
        except ValidationError as exc:
            errors.append(f"{spec_sheet.title}, строка {row_number}: {'; '.join(exc.messages)}")

    for normalized, names in {**purchased_variants, **variants}.items():
        all_names = set(variants.get(normalized, set())) | set(purchased_variants.get(normalized, set()))
        if len(all_names) > 1:
            warnings.append("Одинаковое наименование объединено: " + " / ".join(sorted(all_names)))

    rows = list(grouped.values())
    for row in rows:
        existing = PurchaseItem.objects.filter(
            order=order, normalized_name=row["normalized_name"], assembly_name__iexact=row["assembly_name"],
        ).order_by("id").first()
        row["existing_id"] = existing.id if existing else None
        row["action"] = "Обновить" if existing else "Добавить"
    if not rows and not errors:
        errors.append("В спецификации нет строк для загрузки")
    return rows, errors, warnings


@transaction.atomic
def save_purchase_preview(order, rows):
    saved = 0
    for row in rows:
        item = PurchaseItem.objects.select_for_update().filter(
            order=order, normalized_name=row["normalized_name"], assembly_name__iexact=row["assembly_name"],
        ).order_by("id").first()
        assembly_ref = None
        if row["assembly_name"]:
            assembly_ref = OrderItem.objects.filter(order=order, item__name__icontains=row["assembly_name"]).first()
        if item is None:
            item = PurchaseItem(order=order, purchase_status="pending")
        item.assembly_ref = assembly_ref
        item.item_name = row["item_name"]
        item.normalized_name = row["normalized_name"]
        item.designation = row.get("designation", "")
        item.assembly_name = row["assembly_name"]
        # Re-import replaces the specification values; it must not double them.
        item.quantity_required = row["quantity_required"]
        item.quantity_purchased = row["quantity_purchased"]
        item.save()
        saved += 1
    return saved

