from collections import OrderedDict, defaultdict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from scanner.models import Order, OrderItem
from scanner.purchase_models import PurchaseItem, PurchaseTransaction
from scanner.purchase_normalization import clean_purchase_name, normalize_purchase_name


def _headers(sheet):
    return [" ".join(str(cell.value or "").strip().casefold().replace("ё", "е").split()) for cell in sheet[1]]


def _index(headers, aliases):
    for alias in aliases:
        for index, header in enumerate(headers):
            if alias in header:
                return index
    return None


def _quantity(value, label):
    if value in (None, ""):
        return Decimal("0")
    try:
        number = Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"{label}: некорректное число «{value}»")
    if not number.is_finite() or number < 0:
        raise ValidationError(f"{label}: укажите неотрицательное количество")
    if number.as_tuple().exponent < -3:
        raise ValidationError(f"{label}: допускается не более трёх знаков после запятой")
    return number


def parse_purchase_workbook(workbook, order=None, preparation=None):
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

    purchased = defaultdict(lambda: Decimal("0"))
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
                purchased[key] += _quantity(row[quantity_index] if quantity_index is not None and quantity_index < len(row) else 0, "Закуплено")
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
            required = _quantity(row[required_index] if required_index < len(row) else 0, "Требуется")
            if required <= 0:
                raise ValidationError("требуемое количество должно быть больше нуля")
            assembly = " ".join(str(row[assembly_index] or "").strip().split()) if assembly_index is not None and assembly_index < len(row) else ""
            designation = " ".join(str(row[designation_index] or "").strip().split()) if designation_index is not None and designation_index < len(row) else ""
            normalized = normalize_purchase_name(name)
            key = (normalized, assembly.casefold())
            variants[normalized].add(raw_name)
            if key not in grouped:
                direct_purchased = _quantity(row[purchased_index] if purchased_index is not None and purchased_index < len(row) else 0, "Закуплено")
                total_purchased = purchased.get(normalized, direct_purchased if purchased_index is not None else required)
                grouped[key] = {
                    "item_name": name, "normalized_name": normalized, "designation": designation,
                    "assembly_name": assembly, "quantity_required": Decimal("0"),
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
        target = PurchaseItem.objects.filter(
            normalized_name=row["normalized_name"], assembly_name__iexact=row["assembly_name"],
        )
        if order is not None:
            target = target.filter(order=order)
        elif preparation is not None:
            target = target.filter(order__isnull=True, preparation=preparation)
        else:
            target = target.none()
        existing = target.order_by("id").first()
        row["existing_id"] = existing.id if existing else None
        row["action"] = "Обновить" if existing else "Добавить"
    if not rows and not errors:
        errors.append("В спецификации нет строк для загрузки")
    return rows, errors, warnings


def enrich_purchase_preview(rows, order=None, preparation=None):
    """Calculate the final stock state without writing anything to the DB."""
    rows = [dict(row) for row in rows]
    target = PurchaseItem.objects.none()
    if order is not None:
        target = PurchaseItem.objects.filter(order=order)
    elif preparation is not None:
        target = PurchaseItem.objects.filter(order__isnull=True, preparation=preparation)

    existing_items = list(target)
    existing_exact = {
        (item.normalized_name, (item.assembly_name or "").casefold()): item
        for item in existing_items
    }
    existing_by_name = defaultdict(list)
    for item in existing_items:
        existing_by_name[item.normalized_name].append(item)

    incoming_by_name = defaultdict(list)
    for row in rows:
        incoming_by_name[row["normalized_name"]].append(row)

    issued_total = defaultdict(lambda: Decimal("0"))
    issued_to_assemblies = defaultdict(lambda: Decimal("0"))
    if order is not None:
        movements = PurchaseTransaction.objects.filter(
            purchase_item__order=order, transaction_type="out",
        ).values("purchase_item__normalized_name").annotate(total=Sum("quantity"))
        for movement in movements:
            issued_total[movement["purchase_item__normalized_name"]] = movement["total"] or Decimal("0")
        assembly_movements = PurchaseTransaction.objects.filter(
            purchase_item__order=order, transaction_type="out", is_general_use=False,
        ).values("purchase_item__normalized_name").annotate(total=Sum("quantity"))
        for movement in assembly_movements:
            issued_to_assemblies[movement["purchase_item__normalized_name"]] = movement["total"] or Decimal("0")

    group_state = {}
    for normalized_name, incoming_rows in incoming_by_name.items():
        current_items = existing_by_name.get(normalized_name, [])
        required_after = sum(
            (item.quantity_required or Decimal("0") for item in current_items),
            Decimal("0"),
        )
        for row in incoming_rows:
            current = existing_exact.get((normalized_name, (row["assembly_name"] or "").casefold()))
            if current:
                required_after -= current.quantity_required or Decimal("0")
            required_after += row["quantity_required"] or Decimal("0")

        purchased_before = max(
            (item.quantity_purchased or Decimal("0") for item in current_items),
            default=Decimal("0"),
        )
        purchased_after = max(
            (row["quantity_purchased"] or Decimal("0") for row in incoming_rows),
            default=purchased_before,
        )
        available_after = max(purchased_after - issued_total[normalized_name], Decimal("0"))
        demand_after = max(required_after - issued_to_assemblies[normalized_name], Decimal("0"))
        reserved_after = min(available_after, demand_after)
        deficit_after = max(demand_after - available_after, Decimal("0"))
        free_after = max(available_after - demand_after, Decimal("0"))
        group_state[normalized_name] = {
            "required_after_total": required_after,
            "purchased_before": purchased_before,
            "purchased_after": purchased_after,
            "issued_total": issued_total[normalized_name],
            "available_after": available_after,
            "reserved_after": reserved_after,
            "free_after": free_after,
            "deficit_after": deficit_after,
        }

    for row in rows:
        key = (row["normalized_name"], (row["assembly_name"] or "").casefold())
        current = existing_exact.get(key)
        changes = []
        if current is None:
            action_code, action = "add", "Новая"
            current_required = Decimal("0")
        else:
            current_required = current.quantity_required or Decimal("0")
            if current_required != row["quantity_required"]:
                changes.append("изменится потребность")
            if (current.quantity_purchased or Decimal("0")) != row["quantity_purchased"]:
                changes.append("изменится закупленное количество")
            if current.item_name != row["item_name"]:
                changes.append("уточнится наименование")
            if (current.designation or "") != (row.get("designation") or ""):
                changes.append("изменится обозначение")
            if changes:
                action_code, action = "update", "Скорректировать"
            else:
                action_code, action = "same", "Без изменений"
        row.update(group_state[row["normalized_name"]])
        row.update({
            "existing_id": current.id if current else None,
            "action_code": action_code,
            "action": action,
            "current_required": current_required,
            "changes": changes,
            "has_deficit": group_state[row["normalized_name"]]["deficit_after"] > 0,
        })
    return rows


@transaction.atomic
def save_purchase_preview(order, rows, preparation=None):
    saved = 0
    for row in rows:
        target = PurchaseItem.objects.select_for_update().filter(
            normalized_name=row["normalized_name"], assembly_name__iexact=row["assembly_name"],
        )
        if order is not None:
            target = target.filter(order=order)
        else:
            target = target.filter(order__isnull=True, preparation=preparation)
        item = target.order_by("id").first()
        assembly_ref = None
        if order is not None and row["assembly_name"]:
            assembly_ref = OrderItem.objects.filter(order=order, item__name__icontains=row["assembly_name"]).first()
        if item is None:
            item = PurchaseItem(order=order, preparation=preparation, purchase_status="pending")
        item.order = order
        item.preparation = preparation
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
