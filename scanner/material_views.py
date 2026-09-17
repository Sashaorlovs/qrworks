from collections import defaultdict, OrderedDict
from decimal import Decimal
from io import BytesIO
import uuid

import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from scanner.material_models import (
    AuxiliaryMaterialLot,
    AuxiliaryMaterialRequirement,
    AuxiliaryMaterialTransaction,
    MaterialGrade,
    MaterialRequirement,
    MaterialRequest,
    MaterialStockLot,
    MaterialTransaction,
    WarehouseIssuer,
    ZERO,
)
from scanner.material_services import (
    available_for_requirement,
    create_material_request,
    decimal_value,
    issue_request_lines,
    validate_material_geometry,
)
from scanner.material_stock_import import (
    create_stock_lots_from_preview,
    group_auxiliary_lots,
    group_stock_lots,
    parse_stock_workbook,
    reconcile_stock_from_preview,
)
from scanner.models import Employee, Order, OrderItem


def _can_use_material_warehouse(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return hasattr(user, "employee") and user.employee.role in {"admin", "dispatcher", "storekeeper", "supervisor", "technologist"}


material_access_required = user_passes_test(_can_use_material_warehouse)


MM_PER_METER = Decimal("1000")


def _millimeters_from_meters(value, default=ZERO):
    meters = decimal_value(value, None)
    return default if meters is None else meters * MM_PER_METER


def _meters(value):
    return (value or ZERO) / MM_PER_METER


def _row_value(row, headers, aliases, default=None):
    for alias in aliases:
        for index, header in enumerate(headers):
            if alias in header:
                return row[index] if index < len(row) else default
    return default


@material_access_required
def material_home(request):
    q = request.GET.get("q", "").strip()
    orders = Order.objects.filter(
        Q(material_requirements__isnull=False) | Q(auxiliary_material_requirements__isnull=False)
    ).distinct().order_by("-id")
    if q:
        orders = orders.filter(Q(order_number__icontains=q) | Q(full_name__icontains=q) | Q(project__icontains=q))
    rows = []
    for order in orders:
        requirements = order.material_requirements.all()
        auxiliary_requirements = order.auxiliary_material_requirements.all()
        rows.append({
            "order": order,
            "positions": requirements.count() + auxiliary_requirements.count(),
            "mass": requirements.aggregate(total=Sum("calculated_mass_kg"))["total"] or ZERO,
            "requests": order.material_requests.count(),
        })
    destinations = set(MaterialRequirement.objects.filter(order__isnull=True).values_list("destination", flat=True))
    destinations.update(AuxiliaryMaterialRequirement.objects.filter(order__isnull=True).values_list("destination", flat=True))
    general_rows = []
    for destination in sorted(destinations, key=lambda value: (value or "").casefold()):
        requirements = MaterialRequirement.objects.filter(order__isnull=True, destination=destination)
        auxiliary_requirements = AuxiliaryMaterialRequirement.objects.filter(order__isnull=True, destination=destination)
        general_rows.append({
            "destination": destination,
            "label": destination or "Без указанного назначения",
            "positions": requirements.count() + auxiliary_requirements.count(),
            "mass": requirements.aggregate(total=Sum("calculated_mass_kg"))["total"] or ZERO,
            "requests": MaterialRequest.objects.filter(order__isnull=True, destination=destination).count(),
        })
    return render(request, "scanner/material_home.html", {
        "rows": rows,
        "general_rows": general_rows,
        "orders": Order.objects.order_by("-id")[:100],
        "q": q,
        "stock_mass": MaterialStockLot.objects.aggregate(total=Sum("mass_remaining_kg"))["total"] or ZERO,
        "open_requests": MaterialRequest.objects.filter(status__in=["open", "partial"]).count(),
    })


@material_access_required
@require_POST
def material_import(request):
    scope_mode = request.POST.get("scope_mode", "order").strip()
    scope = request.POST.get("order_id", "").strip()
    destination = ""
    if scope == "general":
        scope_mode = "manual"
        destination = "Общепроизводственные нужды"
    if scope_mode == "manual":
        destination = request.POST.get("manual_destination", "").strip() or destination
        if not destination:
            messages.error(request, "Укажите назначение материала: например, стеллаж, приспособление или заявка на ремонт.")
            return redirect("material_home")
        order = None
    else:
        if not scope:
            messages.error(request, "Выберите проект / заказ.")
            return redirect("material_home")
        order = get_object_or_404(Order, pk=scope)
    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, "Выберите файл Excel.")
        return redirect("material_home")
    try:
        workbook = openpyxl.load_workbook(upload, data_only=True)
        standard_sheets = {"Лист", "Труба", "Прокат", "Прочие материалы"}
        if standard_sheets.intersection(workbook.sheetnames):
            prepared, errors = parse_stock_workbook(workbook)
            if errors:
                messages.error(request, "Файл не загружен. " + " | ".join(errors[:12]))
                return redirect("material_home")
            metal_count = auxiliary_count = 0
            with transaction.atomic():
                for row in prepared:
                    assembly_name = row.get("assembly_name", "").strip()
                    if row["record_type"] == "auxiliary":
                        lookup = {
                            "order": order,
                            "destination": destination,
                            "assembly_name": assembly_name,
                            "category": row["category"],
                            "name": row["name"],
                            "brand": row.get("brand", ""),
                            "characteristics": row.get("characteristics", ""),
                            "unit": row["unit"],
                        }
                        AuxiliaryMaterialRequirement.objects.update_or_create(
                            **lookup,
                            defaults={
                                "quantity_required": decimal_value(row["quantity"]),
                                "package_description": row.get("package_description", ""),
                                "density_kg_l": decimal_value(row.get("density_kg_l"), None),
                                "thickness_mm": decimal_value(row.get("thickness_mm"), None),
                                "width_mm": decimal_value(row.get("width_mm"), None),
                                "length_mm": decimal_value(row.get("length_mm"), None),
                                "mesh_cell_width_mm": decimal_value(row.get("mesh_cell_width_mm"), None),
                                "mesh_cell_height_mm": decimal_value(row.get("mesh_cell_height_mm"), None),
                                "wire_diameter_mm": decimal_value(row.get("wire_diameter_mm"), None),
                            },
                        )
                        auxiliary_count += 1
                        continue
                    grade = MaterialGrade.objects.get(pk=row["grade_id"])
                    lookup = {
                        "order": order,
                        "destination": destination,
                        "item_name": row["name"],
                        "assembly_name": assembly_name,
                        "grade": grade,
                        "profile_type": row["profile_type"],
                        "profile_name": row.get("profile_name", ""),
                    }
                    assembly_ref = None
                    if assembly_name and order:
                        assembly_ref = OrderItem.objects.filter(order=order, item__name__icontains=assembly_name).first()
                    MaterialRequirement.objects.update_or_create(
                        **lookup,
                        defaults={
                            "assembly_ref": assembly_ref,
                            "quantity_required": decimal_value(row["quantity"]),
                            "total_length_required_mm": decimal_value(row.get("total_length_mm")),
                            "piece_length_mm": decimal_value(row.get("piece_length_mm"), None),
                            "thickness_mm": decimal_value(row.get("thickness_mm"), None),
                            "width_mm": decimal_value(row.get("width_mm"), None),
                            "height_mm": decimal_value(row.get("height_mm"), None),
                            "outer_diameter_mm": decimal_value(row.get("outer_diameter_mm"), None),
                            "wall_thickness_mm": decimal_value(row.get("wall_thickness_mm"), None),
                            "kg_per_meter": decimal_value(row.get("kg_per_meter"), None),
                            "unit_mass_kg": decimal_value(row.get("unit_mass_kg"), None),
                        },
                    )
                    metal_count += 1
            messages.success(
                request,
                f"Загружено позиций: {metal_count + auxiliary_count} (металл — {metal_count}, прочие материалы — {auxiliary_count}).",
            )
            if order:
                return redirect("material_order_detail", order_id=order.id)
            return redirect("material_general_detail")
        sheet = workbook["Материалы"] if "Материалы" in workbook.sheetnames else workbook.active
        headers = [str(cell.value or "").strip().casefold() for cell in sheet[1]]
    except Exception as exc:
        messages.error(request, f"Не удалось прочитать файл: {exc}")
        return redirect("material_home")

    profile_aliases = {label.casefold(): code for code, label in MaterialRequirement.PROFILE_CHOICES}
    profile_aliases.update({code: code for code, _ in MaterialRequirement.PROFILE_CHOICES})
    prepared, errors = [], []
    for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if not row or all(value in (None, "") for value in row):
            continue
        try:
            name = str(_row_value(row, headers, ["наименование", "материал / назначение"], "") or "").strip()
            grade_name = str(_row_value(row, headers, ["марка материала", "марка"], "") or "").strip()
            profile_raw = str(_row_value(row, headers, ["тип профиля", "профиль"], "") or "").strip()
            if not name or not grade_name or not profile_raw:
                raise ValidationError("обязательны Наименование, Марка материала и Тип профиля")
            profile = profile_aliases.get(profile_raw.casefold())
            if not profile:
                raise ValidationError(f"неизвестный тип профиля «{profile_raw}»")
            grade = MaterialGrade.objects.filter(name__iexact=grade_name, is_active=True).first()
            if not grade:
                raise ValidationError(f"марка «{grade_name}» отсутствует в справочнике плотностей")
            quantity = decimal_value(_row_value(row, headers, ["количество", "кол-во", "требуется, шт"], 0))
            length_total = decimal_value(_row_value(row, headers, ["общая длина", "требуется длины"], 0))
            if quantity <= 0 and length_total <= 0:
                raise ValidationError("укажите количество или общую длину")
            data = {
                "order": order,
                "destination": destination,
                "item_name": name,
                "assembly_name": str(_row_value(row, headers, ["подсборка", "узел"], "") or "").strip(),
                "grade": grade,
                "profile_type": profile,
                "profile_name": str(_row_value(row, headers, ["сортамент", "обозначение профиля"], "") or "").strip(),
                "quantity_required": quantity,
                "total_length_required_mm": length_total,
                "thickness_mm": decimal_value(_row_value(row, headers, ["толщина, мм", "толщина"], None), None),
                "width_mm": decimal_value(_row_value(row, headers, ["ширина, мм", "ширина"], None), None),
                "height_mm": decimal_value(_row_value(row, headers, ["высота, мм", "высота"], None), None),
                "outer_diameter_mm": decimal_value(_row_value(row, headers, ["наружный диаметр", "диаметр, мм", "диаметр"], None), None),
                "wall_thickness_mm": decimal_value(_row_value(row, headers, ["стенка, мм", "толщина стенки"], None), None),
                "piece_length_mm": decimal_value(_row_value(row, headers, ["длина единицы", "длина, мм"], None), None),
                "kg_per_meter": decimal_value(_row_value(row, headers, ["кг/м", "масса 1 м"], None), None),
                "unit_mass_kg": decimal_value(_row_value(row, headers, ["масса единицы", "кг/шт"], None), None),
                "notes": str(_row_value(row, headers, ["примечание"], "") or "").strip(),
            }
            if data["assembly_name"] and order:
                data["assembly_ref"] = OrderItem.objects.filter(order=order, item__name__icontains=data["assembly_name"]).first()
            probe = MaterialRequirement(**data)
            validate_material_geometry(probe)
            prepared.append(data)
        except ValidationError as exc:
            errors.append(f"Строка {row_number}: {'; '.join(exc.messages)}")
        except Exception as exc:
            errors.append(f"Строка {row_number}: {exc}")
    if errors:
        messages.error(request, "Файл не загружен. " + " | ".join(errors[:12]))
        return redirect("material_home")
    with transaction.atomic():
        for data in prepared:
            lookup = {
                "order": order,
                "destination": destination,
                "item_name": data["item_name"],
                "assembly_name": data["assembly_name"],
                "grade": data["grade"],
                "profile_type": data["profile_type"],
                "profile_name": data["profile_name"],
            }
            defaults = {key: value for key, value in data.items() if key not in lookup and key != "order"}
            MaterialRequirement.objects.update_or_create(**lookup, defaults=defaults)
    messages.success(request, f"Загружено позиций: {len(prepared)}. Площадь и масса рассчитаны автоматически.")
    if order:
        return redirect("material_order_detail", order_id=order.id)
    return redirect("material_general_detail")


@material_access_required
def material_import_template(request):
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)

    def make_sheet(title, headers, rows):
        sheet = workbook.create_sheet(title)
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{sheet.cell(1, len(headers)).column_letter}{sheet.max_row}"
        for cell in sheet[1]:
            cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")
            cell.fill = openpyxl.styles.PatternFill("solid", fgColor="163A66")
            cell.alignment = openpyxl.styles.Alignment(wrap_text=True)
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(30, max(13, max(len(str(c.value or "")) for c in column) + 2))

    make_sheet("Лист", ["Наименование", "Узел / подсборка", "Марка материала", "Толщина, мм", "Ширина, мм", "Длина листа, м", "Количество"], [["Лист 8 мм", "Корпус", "Ст3", 8, 1500, 6, 2]])
    make_sheet("Труба", ["Наименование", "Узел / подсборка", "Вид трубы", "Марка материала", "Сортамент", "Наружный диаметр, мм", "Ширина, мм", "Высота, мм", "Толщина стенки, мм", "Длина куска, м", "Количество"], [["Труба 57×3,5", "Рама", "Труба круглая", "Сталь", "57×3,5", 57, "", "", 3.5, 6, 4]])
    make_sheet("Прокат", ["Наименование", "Узел / подсборка", "Вид профиля", "Марка материала", "Сортамент", "Диаметр, мм", "Ширина, мм", "Высота, мм", "Толщина, мм", "Длина куска, м", "Количество", "Масса 1 м, кг"], [
        ["Круг 40", "Вал", "Круг", "Сталь", "Круг 40", 40, "", "", "", 3, 2, ""],
        ["Квадрат 20", "Рама", "Квадрат", "Сталь", "20×20", "", 20, "", "", 6, 2, ""],
        ["Шестигранник 24", "Крепление", "Шестигранник", "Сталь", "S24", "", 24, "", "", 3, 2, ""],
    ])
    make_sheet("Прочие материалы", ["Категория", "Наименование", "Узел / подсборка", "Марка / производитель", "Характеристика", "Единица учета", "Количество", "Тара / упаковка", "Плотность, кг/л", "Толщина, мм", "Ширина, мм", "Длина, м", "Ячейка X, мм", "Ячейка Y, мм", "Диаметр проволоки, мм"], [
        ["Краска / покрытие", "Эмаль ПФ-115 синяя", "Корпус", "Лакра", "RAL 5005", "л", 20, "4 банки по 5 л", 1.2, "", "", "", "", "", ""],
        ["Сетка", "Сетка сварная 50×50", "Ограждение", "", "Карта сетки", "м²", 12, "", "", "", 1000, 2, 50, 50, 3],
    ])
    density = workbook.create_sheet("Справочник плотностей")
    density.append(["Марка материала", "Группа", "Плотность, кг/м³"])
    for grade in MaterialGrade.objects.filter(is_active=True):
        density.append([grade.name, grade.get_category_display(), float(grade.density_kg_m3)])
    instruction = workbook.create_sheet("Инструкция", 0)
    instruction.append(["Загрузка потребности материалов к проекту"])
    instruction.append(["1. Проект выбирается на сайте; в Excel его указывать не нужно."])
    instruction.append(["2. Формат сортамента совпадает со складом: Лист, Труба, Прокат и Прочие материалы."])
    instruction.append(["3. В поле «Узел / подсборка» укажите, для какой части изделия требуется материал."])
    instruction.append(["4. Краску, растворитель, смазку, резину, сетку и расходники вносите в «Прочие материалы»."])
    instruction.append(["5. Для уголка, швеллера, двутавра и полособульба указывайте сортамент и массу 1 м."])
    instruction.column_dimensions["A"].width = 110
    instruction["A1"].font = openpyxl.styles.Font(bold=True, size=14, color="163A66")
    output = BytesIO()
    workbook.save(output)
    response = HttpResponse(output.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="material_import_template.xlsx"'
    return response


@material_access_required
def material_order_detail(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    requirements = list(order.material_requirements.select_related("grade", "assembly_ref__item"))
    q = request.GET.get("q", "").strip()
    if q:
        requirements = [item for item in requirements if q.casefold() in f"{item.item_name} {item.assembly_name} {item.grade.name} {item.profile_name}".casefold()]
    for item in requirements:
        issued = MaterialTransaction.objects.filter(request_line__requirement=item, transaction_type="out").aggregate(
            quantity=Sum("quantity"), length=Sum("length_mm"), mass=Sum("mass_kg")
        )
        item.issued_quantity = issued["quantity"] or ZERO
        item.issued_length_mm = issued["length"] or ZERO
        item.issued_mass_kg = issued["mass"] or ZERO
        item.available = available_for_requirement(item)
        item.total_length_required_m = _meters(item.total_length_required_mm)
        item.issued_length_m = _meters(item.issued_length_mm)
        if item.is_linear:
            item.shortage_length_mm = max(
                ZERO, item.total_length_required_mm - item.issued_length_mm - item.available["length_mm"]
            )
            item.shortage_quantity = ZERO
            item.shortage_mass_kg = item.calculate_mass(ZERO, item.shortage_length_mm)
            item.shortage_length_m = _meters(item.shortage_length_mm)
        else:
            item.shortage_quantity = max(
                ZERO, item.quantity_required - item.issued_quantity - item.available["quantity"]
            )
            item.shortage_length_mm = ZERO
            item.shortage_mass_kg = item.calculate_mass(item.shortage_quantity, ZERO)
    auxiliary_requirements = list(order.auxiliary_material_requirements.all())
    if q:
        auxiliary_requirements = [item for item in auxiliary_requirements if q.casefold() in f"{item.name} {item.assembly_name} {item.brand} {item.characteristics}".casefold()]
    for item in auxiliary_requirements:
        lots = AuxiliaryMaterialLot.objects.filter(
            category=item.category, unit=item.unit,
            name__iexact=item.name, quantity_remaining__gt=0,
        ).select_related("order")
        def same(left, right):
            return (left or "").strip().casefold() == (right or "").strip().casefold()
        matching = [lot for lot in lots if same(lot.brand, item.brand) and same(lot.characteristics, item.characteristics)]
        item.available_quantity = sum((lot.quantity_remaining for lot in matching), ZERO)
        grouped = defaultdict(lambda: ZERO)
        for lot in matching:
            grouped[lot.order.order_number if lot.order else "Общий склад"] += lot.quantity_remaining
        item.available_breakdown = [{"label": label, "quantity": quantity} for label, quantity in grouped.items()]
    return render(request, "scanner/material_order_detail.html", {
        "order": order, "requirements": requirements,
        "auxiliary_requirements": auxiliary_requirements, "q": q,
        "is_general": False,
    })


@material_access_required
def material_general_detail(request):
    destination = request.GET.get("destination", "").strip()
    requirement_qs = MaterialRequirement.objects.filter(order__isnull=True)
    auxiliary_qs = AuxiliaryMaterialRequirement.objects.filter(order__isnull=True)
    if destination:
        requirement_qs = requirement_qs.filter(destination=destination)
        auxiliary_qs = auxiliary_qs.filter(destination=destination)
    requirements = list(requirement_qs.select_related("grade", "assembly_ref__item"))
    q = request.GET.get("q", "").strip()
    if q:
        requirements = [item for item in requirements if q.casefold() in f"{item.item_name} {item.assembly_name} {item.grade.name} {item.profile_name}".casefold()]
    for item in requirements:
        issued = MaterialTransaction.objects.filter(request_line__requirement=item, transaction_type="out").aggregate(
            quantity=Sum("quantity"), length=Sum("length_mm"), mass=Sum("mass_kg")
        )
        item.issued_quantity = issued["quantity"] or ZERO
        item.issued_length_mm = issued["length"] or ZERO
        item.issued_mass_kg = issued["mass"] or ZERO
        item.available = available_for_requirement(item)
        item.total_length_required_m = _meters(item.total_length_required_mm)
        item.issued_length_m = _meters(item.issued_length_mm)
        if item.is_linear:
            item.shortage_length_mm = max(
                ZERO, item.total_length_required_mm - item.issued_length_mm - item.available["length_mm"]
            )
            item.shortage_quantity = ZERO
            item.shortage_mass_kg = item.calculate_mass(ZERO, item.shortage_length_mm)
            item.shortage_length_m = _meters(item.shortage_length_mm)
        else:
            item.shortage_quantity = max(
                ZERO, item.quantity_required - item.issued_quantity - item.available["quantity"]
            )
            item.shortage_length_mm = ZERO
            item.shortage_mass_kg = item.calculate_mass(item.shortage_quantity, ZERO)
    auxiliary_requirements = list(auxiliary_qs)
    if q:
        auxiliary_requirements = [item for item in auxiliary_requirements if q.casefold() in f"{item.name} {item.assembly_name} {item.brand} {item.characteristics}".casefold()]
    for item in auxiliary_requirements:
        lots = AuxiliaryMaterialLot.objects.filter(
            category=item.category, unit=item.unit,
            name__iexact=item.name, quantity_remaining__gt=0,
        ).select_related("order")
        def same(left, right):
            return (left or "").strip().casefold() == (right or "").strip().casefold()
        matching = [lot for lot in lots if same(lot.brand, item.brand) and same(lot.characteristics, item.characteristics)]
        item.available_quantity = sum((lot.quantity_remaining for lot in matching), ZERO)
        grouped = defaultdict(lambda: ZERO)
        for lot in matching:
            grouped[lot.order.order_number if lot.order else "Общий склад"] += lot.quantity_remaining
        item.available_breakdown = [{"label": label, "quantity": quantity} for label, quantity in grouped.items()]
    return render(request, "scanner/material_order_detail.html", {
        "order": None, "requirements": requirements,
        "auxiliary_requirements": auxiliary_requirements, "q": q,
        "is_general": True,
        "destination": destination,
        "scope_title": destination or "Внутренние работы без проекта",
    })


@material_access_required
@require_POST
def material_order_create_request(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    requirements = list(MaterialRequirement.objects.filter(order=order, id__in=request.POST.getlist("requirement_ids")).select_related("grade"))
    if not requirements:
        messages.error(request, "Выберите хотя бы одну позицию.")
        return redirect("material_order_detail", order_id=order.id)
    try:
        document = create_material_request(order, requirements, request.user, request.POST.get("purpose", ""))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("material_order_detail", order_id=order.id)
    messages.success(request, f"Заявка {document.number} сформирована.")
    return redirect("material_request_detail", request_id=document.id)


@material_access_required
@require_POST
def material_general_create_request(request):
    destination = request.POST.get("destination", "").strip()
    requirements = list(MaterialRequirement.objects.filter(
        order__isnull=True, destination=destination,
        id__in=request.POST.getlist("requirement_ids")
    ).select_related("grade"))
    if not requirements:
        messages.error(request, "Выберите хотя бы одну позицию.")
        return redirect("material_general_detail")
    try:
        document = create_material_request(
            None, requirements, request.user, request.POST.get("purpose", ""), destination=destination,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("material_general_detail")
    messages.success(request, f"Заявка {document.number} сформирована.")
    return redirect("material_request_detail", request_id=document.id)


@material_access_required
def material_stock(request):
    if request.method == "POST":
        try:
            grade = get_object_or_404(MaterialGrade, pk=request.POST.get("grade_id"), is_active=True)
            lot = MaterialStockLot(
                order_id=request.POST.get("order_id") or None,
                grade=grade,
                name=request.POST.get("name", "").strip(),
                profile_type=request.POST.get("profile_type", ""),
                profile_name=request.POST.get("profile_name", "").strip(),
                batch_number=request.POST.get("batch_number", "").strip(),
                storage_location=request.POST.get("storage_location", "").strip(),
                quantity_initial=decimal_value(request.POST.get("quantity")),
                length_initial_mm=_millimeters_from_meters(request.POST.get("length_m")),
                thickness_mm=decimal_value(request.POST.get("thickness_mm"), None),
                width_mm=decimal_value(request.POST.get("width_mm"), None),
                height_mm=decimal_value(request.POST.get("height_mm"), None),
                outer_diameter_mm=decimal_value(request.POST.get("outer_diameter_mm"), None),
                wall_thickness_mm=decimal_value(request.POST.get("wall_thickness_mm"), None),
                piece_length_mm=_millimeters_from_meters(request.POST.get("piece_length_m"), None),
                kg_per_meter=decimal_value(request.POST.get("kg_per_meter"), None),
                unit_mass_kg=decimal_value(request.POST.get("unit_mass_kg"), None),
                created_by=request.user,
            )
            if not lot.name or not lot.profile_type:
                raise ValidationError("Укажите наименование и тип профиля.")
            if lot.quantity_initial <= 0 and lot.length_initial_mm <= 0:
                raise ValidationError("Укажите принятое количество или общую длину.")
            validate_material_geometry(lot)
            lot.initialize_balances()
            lot.save()
            MaterialTransaction.objects.create(
                stock_lot=lot, order=lot.order, transaction_type="in", quantity=lot.quantity_initial,
                length_mm=lot.length_initial_mm, mass_kg=lot.mass_initial_kg,
                basis=request.POST.get("basis", "Приход материала"), created_by=request.user,
            )
            messages.success(request, f"Приход сохранён: {lot.name}, {lot.mass_initial_kg} кг.")
            return redirect("material_stock")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        except Exception as exc:
            messages.error(request, f"Не удалось сохранить приход: {exc}")
    q = request.GET.get("q", "").strip()
    lots = MaterialStockLot.objects.select_related("grade", "order").filter(
        Q(profile_type="sheet", quantity_remaining__gt=0)
        | (~Q(profile_type="sheet") & Q(length_remaining_mm__gt=0))
    )
    if q:
        lots = lots.filter(Q(name__icontains=q) | Q(grade__name__icontains=q) | Q(profile_name__icontains=q) | Q(batch_number__icontains=q))
    auxiliary_lots = AuxiliaryMaterialLot.objects.select_related("order").filter(quantity_remaining__gt=0)
    if q:
        auxiliary_lots = auxiliary_lots.filter(
            Q(name__icontains=q) | Q(brand__icontains=q) | Q(characteristics__icontains=q)
            | Q(batch_number__icontains=q) | Q(storage_location__icontains=q)
        )
    groups = group_stock_lots(list(lots))
    return render(request, "scanner/material_stock.html", {
        "groups": groups, "auxiliary_groups": group_auxiliary_lots(list(auxiliary_lots)),
        "q": q, "grades": MaterialGrade.objects.filter(is_active=True),
        "orders": Order.objects.order_by("-id")[:100], "profiles": MaterialRequirement.PROFILE_CHOICES,
        "employees": Employee.objects.filter(is_active=True).order_by("last_name", "first_name", "middle_name"),
        "warehouse_issuers": WarehouseIssuer.objects.filter(is_active=True),
        "auxiliary_categories": AuxiliaryMaterialLot.CATEGORY_CHOICES,
        "auxiliary_units": AuxiliaryMaterialLot.UNIT_CHOICES,
    })


@material_access_required
@require_POST
def material_auxiliary_receipt(request):
    try:
        quantity = decimal_value(request.POST.get("quantity"))
        if quantity <= 0:
            raise ValidationError("Количество должно быть больше нуля.")
        category = request.POST.get("category", "")
        unit = request.POST.get("unit", "")
        if category not in dict(AuxiliaryMaterialLot.CATEGORY_CHOICES):
            raise ValidationError("Выберите категорию материала.")
        if unit not in dict(AuxiliaryMaterialLot.UNIT_CHOICES):
            raise ValidationError("Выберите единицу учёта.")
        name = request.POST.get("name", "").strip()
        if not name:
            raise ValidationError("Укажите наименование материала.")
        expiry_raw = request.POST.get("expiry_date", "").strip()
        expiry_date = parse_date(expiry_raw) if expiry_raw else None
        if expiry_raw and not expiry_date:
            raise ValidationError("Некорректный срок годности.")
        with transaction.atomic():
            lot = AuxiliaryMaterialLot.objects.create(
                order_id=request.POST.get("order_id") or None,
                category=category,
                name=name,
                brand=request.POST.get("brand", "").strip(),
                characteristics=request.POST.get("characteristics", "").strip(),
                unit=unit,
                quantity_initial=quantity,
                quantity_remaining=quantity,
                package_description=request.POST.get("package_description", "").strip(),
                density_kg_l=decimal_value(request.POST.get("density_kg_l"), None),
                thickness_mm=decimal_value(request.POST.get("thickness_mm"), None),
                width_mm=decimal_value(request.POST.get("width_mm"), None),
                length_mm=_millimeters_from_meters(request.POST.get("length_m"), None),
                mesh_cell_width_mm=decimal_value(request.POST.get("mesh_cell_width_mm"), None),
                mesh_cell_height_mm=decimal_value(request.POST.get("mesh_cell_height_mm"), None),
                wire_diameter_mm=decimal_value(request.POST.get("wire_diameter_mm"), None),
                expiry_date=expiry_date,
                storage_location=request.POST.get("storage_location", "").strip(),
                created_by=request.user,
            )
            AuxiliaryMaterialTransaction.objects.create(
                stock_lot=lot,
                transaction_type="in",
                quantity=quantity,
                basis=request.POST.get("basis", "").strip() or "Приход материала",
                created_by=request.user,
            )
        messages.success(request, f"Приход сохранён: {lot.name}, {quantity} {lot.get_unit_display()}.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception as exc:
        messages.error(request, f"Не удалось сохранить приход: {exc}")
    return redirect("material_stock")


@material_access_required
@require_POST
def material_auxiliary_issue(request, lot_id):
    try:
        quantity = decimal_value(request.POST.get("quantity"))
        if quantity <= 0:
            raise ValidationError("Количество выдачи должно быть больше нуля.")
        recipient = get_object_or_404(Employee, pk=request.POST.get("recipient_id"), is_active=True)
        issuer = get_object_or_404(WarehouseIssuer, pk=request.POST.get("issuer_id"), is_active=True)
        with transaction.atomic():
            lot = get_object_or_404(AuxiliaryMaterialLot.objects.select_for_update(), pk=lot_id)
            if quantity > lot.quantity_remaining:
                raise ValidationError(
                    f"Нельзя выдать {quantity} {lot.get_unit_display()}: на складе {lot.quantity_remaining}."
                )
            lot.quantity_remaining -= quantity
            lot.save(update_fields=["quantity_remaining"])
            AuxiliaryMaterialTransaction.objects.create(
                stock_lot=lot,
                transaction_type="out",
                quantity=quantity,
                recipient=recipient,
                recipient_name=str(recipient).strip(),
                issuer_name=issuer.name,
                basis=request.POST.get("basis", "").strip() or "Общепроизводственные нужды",
                created_by=request.user,
            )
        messages.success(request, f"Выдано: {lot.name}, {quantity} {lot.get_unit_display()} — {recipient}.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("material_stock")


@material_access_required
def material_stock_import(request):
    preview = None
    errors = []
    token = ""
    selected_order_id = request.POST.get("order_id", "") if request.method == "POST" else ""
    receipt_type = request.POST.get("receipt_type", "receipt") if request.method == "POST" else "receipt"
    common_basis = request.POST.get("basis", "").strip() if request.method == "POST" else ""
    if request.method == "POST" and request.POST.get("action") == "confirm":
        token = request.POST.get("token", "")
        session_key = f"material_stock_import:{token}"
        rows = request.session.pop(session_key, None)
        if not rows:
            messages.error(request, "Предварительная проверка устарела. Загрузите файл ещё раз.")
            return redirect("material_stock_import")
        try:
            if rows[0].get("inventory_reconciliation"):
                lots, adjusted = reconcile_stock_from_preview(rows, request.user)
                messages.success(
                    request,
                    f"Инвентаризация применена: предыдущих складских позиций скорректировано — {adjusted}, фактических позиций и кусков загружено — {len(lots)}.",
                )
                return redirect("material_stock")
            lots = create_stock_lots_from_preview(rows, request.user)
        except ValidationError as exc:
            messages.error(request, "Файл не загружен: " + " ".join(exc.messages))
            return redirect("material_stock_import")
        messages.success(
            request,
            f"Склад загружен: {len(rows)} строк, создано складских позиций и кусков: {len(lots)}.",
        )
        return redirect("material_stock")

    if request.method == "POST":
        upload = request.FILES.get("file")
        if not upload:
            errors.append("Выберите файл Excel.")
        else:
            try:
                workbook = openpyxl.load_workbook(upload, data_only=True)
                workbook_sheet_names = {sheet.title.strip().casefold() for sheet in workbook.worksheets}
                inventory_sections = {
                    "metal": bool(workbook_sheet_names & {"лист", "труба", "прокат", "остатки"}),
                    "auxiliary": "прочие материалы" in workbook_sheet_names,
                }
                preview, errors = parse_stock_workbook(workbook)
            except Exception as exc:
                errors.append(f"Не удалось прочитать файл: {exc}")
            if preview and not errors:
                selected_order = None
                if receipt_type == "inventory":
                    selected_order_id = ""
                elif selected_order_id:
                    selected_order = Order.objects.filter(pk=selected_order_id).first()
                    if not selected_order:
                        errors.append("Выбранный проект не найден.")
                if receipt_type not in {"receipt", "inventory"}:
                    errors.append("Выберите корректный тип загрузки.")
                if not errors:
                    default_basis = "Остатки после инвентаризации" if receipt_type == "inventory" else "Приход материала"
                    for row in preview:
                        row["order_id"] = selected_order.id if selected_order else None
                        row["order_number"] = selected_order.order_number if selected_order else (
                            "Весь физический склад" if receipt_type == "inventory" else "Общий склад"
                        )
                        row["basis"] = common_basis or default_basis
                        row["inventory_reconciliation"] = receipt_type == "inventory"
                        row["inventory_all_scopes"] = receipt_type == "inventory"
                        row["inventory_sections"] = inventory_sections
                lots_count = sum(row["lots_to_create"] for row in preview)
                if not errors and lots_count > 5000:
                    errors.append("За одну загрузку можно создать не более 5000 складских позиций и кусков.")
                elif not errors:
                    token = uuid.uuid4().hex
                    request.session[f"material_stock_import:{token}"] = preview
                    request.session.modified = True

    return render(request, "scanner/material_stock_import.html", {
        "preview": preview,
        "errors": errors,
        "token": token,
        "lots_count": sum(row["lots_to_create"] for row in preview or []),
        "total_mass": sum((decimal_value(row["mass_kg"]) for row in preview or []), ZERO),
        "total_area": sum((decimal_value(row["area_m2"]) for row in preview or []), ZERO),
        "orders": Order.objects.order_by("-id")[:100],
        "selected_order_id": selected_order_id,
        "receipt_type": receipt_type,
        "common_basis": common_basis,
    })


@material_access_required
def material_stock_import_template(request):
    workbook = openpyxl.Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    def make_sheet(title, headers, example):
        sheet = workbook.create_sheet(title)
        sheet.append(headers)
        sheet.append(example)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{sheet.cell(1, len(headers)).column_letter}{sheet.max_row}"
        for cell in sheet[1]:
            cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")
            cell.fill = openpyxl.styles.PatternFill("solid", fgColor="163A66")
            cell.alignment = openpyxl.styles.Alignment(wrap_text=True)
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(
                30, max(13, max(len(str(cell.value or "")) for cell in column) + 2)
            )
        return sheet

    make_sheet(
        "Лист",
        ["Наименование", "Марка материала", "Толщина, мм", "Ширина, мм", "Длина листа, м", "Количество", "Место хранения"],
        ["Лист 8 мм", "Ст3", 8, 1500, 6, 3, "Стеллаж Л-1"],
    )
    make_sheet(
        "Труба",
        ["Наименование", "Вид трубы", "Марка материала", "Сортамент", "Наружный диаметр, мм", "Ширина, мм", "Высота, мм", "Толщина стенки, мм", "Длина куска, м", "Количество", "Место хранения"],
        ["Труба 57×3,5", "Труба круглая", "Сталь", "57×3,5", 57, "", "", 3.5, 6, 5, "Стеллаж Т-2"],
    )
    rolled = make_sheet(
        "Прокат",
        ["Наименование", "Вид профиля", "Марка материала", "Сортамент", "Диаметр, мм", "Ширина, мм", "Высота, мм", "Толщина, мм", "Длина куска, м", "Количество", "Масса 1 м, кг", "Место хранения"],
        ["Круг 40", "Круг", "Сталь", "Круг 40", 40, "", "", "", 3, 2, "", "Стеллаж П-1"],
    )
    rolled.append(["Квадрат 20", "Квадрат", "Сталь", "20×20", "", 20, "", "", 6, 2, "", "Стеллаж П-1"])
    rolled.append(["Шестигранник 24", "Шестигранник", "Сталь", "S24", "", 24, "", "", 3, 2, "", "Стеллаж П-1"])
    auxiliary_headers = [
        "Категория", "Наименование", "Марка / производитель", "Характеристика",
        "Единица учета", "Количество", "Тара / упаковка", "Плотность, кг/л",
        "Толщина, мм", "Ширина, мм", "Длина, м", "Ячейка X, мм", "Ячейка Y, мм",
        "Диаметр проволоки, мм", "Срок годности", "Место хранения",
    ]
    auxiliary = make_sheet(
        "Прочие материалы",
        auxiliary_headers,
        ["Краска / покрытие", "Эмаль ПФ-115 синяя", "Лакра", "RAL 5005", "л", 20,
         "4 банки по 5 л", 1.2, "", "", "", "", "", "", "31.12.2027", "Шкаф 1"],
    )
    auxiliary.append([
        "Сетка", "Сетка сварная 50×50", "", "Карта сетки", "м²", 12, "",
        "", "", 1000, 2, 50, 50, 3, "", "Стеллаж С-1",
    ])
    density = workbook.create_sheet("Справочник плотностей")
    density.append(["Марка материала", "Группа", "Плотность, кг/м³", "ГОСТ / стандарт"])
    for grade in MaterialGrade.objects.filter(is_active=True):
        density.append([grade.name, grade.get_category_display(), float(grade.density_kg_m3), grade.standard])
    for cell in density[1]:
        cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")
        cell.fill = openpyxl.styles.PatternFill("solid", fgColor="163A66")
    density.freeze_panes = "A2"
    for width, letter in zip([28, 24, 22, 28], ["A", "B", "C", "D"]):
        density.column_dimensions[letter].width = width

    instruction = workbook.create_sheet("Инструкция", 0)
    instruction.append(["Загрузка склада материалов"])
    instruction.append(["1. Заполняйте подходящий лист: Лист, Труба, Прокат или Прочие материалы."])
    instruction.append(["2. Длину листа, трубы и проката указывайте в метрах. Толщину, ширину, высоту и диаметр — в миллиметрах."])
    instruction.append(["3. Куски разной длины вносите отдельными строками."])
    instruction.append(["4. Проект и основание прихода выбираются один раз на странице загрузки, а не заполняются в Excel."])
    instruction.append(["5. Марка должна совпадать со справочником плотностей в этом файле."])
    instruction.append(["6. Краски, растворители, смазки, резину, сетку и расходные материалы загружайте на листе «Прочие материалы»."])
    instruction.append(["7. Для квадрата укажите сторону в поле «Ширина», для шестигранника — размер под ключ в этом же поле; масса рассчитается автоматически."])
    instruction.append(["8. Для уголка, швеллера, двутавра и полособульба укажите сортамент и точную массу 1 м из сертификата или таблицы сортамента."])
    instruction.column_dimensions["A"].width = 110
    instruction["A1"].font = openpyxl.styles.Font(bold=True, size=14, color="163A66")

    output = BytesIO()
    workbook.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="material_stock_import_template.xlsx"'
    return response


@material_access_required
def material_stock_export(request):
    """Export active balances in the same shape accepted by inventory import."""
    order_id = request.GET.get("order_id", "").strip()
    selected_order = None
    if order_id:
        selected_order = Order.objects.filter(pk=order_id).first()
        if not selected_order:
            messages.error(request, "Выбранный проект не найден.")
            return redirect("material_stock_import")

    workbook = openpyxl.Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    def make_sheet(title, headers):
        sheet = workbook.create_sheet(title)
        sheet.append(headers)
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")
            cell.fill = openpyxl.styles.PatternFill("solid", fgColor="163A66")
            cell.alignment = openpyxl.styles.Alignment(wrap_text=True)
        return sheet

    sheet_page = make_sheet(
        "Лист",
        ["Наименование", "Марка материала", "Толщина, мм", "Ширина, мм", "Длина листа, м", "Количество", "Место хранения"],
    )
    pipe_page = make_sheet(
        "Труба",
        ["Наименование", "Вид трубы", "Марка материала", "Сортамент", "Наружный диаметр, мм", "Ширина, мм", "Высота, мм", "Толщина стенки, мм", "Длина куска, м", "Количество", "Место хранения"],
    )
    rolled_page = make_sheet(
        "Прокат",
        ["Наименование", "Вид профиля", "Марка материала", "Сортамент", "Диаметр, мм", "Ширина, мм", "Высота, мм", "Толщина, мм", "Длина куска, м", "Количество", "Масса 1 м, кг", "Место хранения"],
    )
    auxiliary_page = make_sheet(
        "Прочие материалы",
        ["Категория", "Наименование", "Марка / производитель", "Характеристика", "Единица учета", "Количество", "Тара / упаковка", "Плотность, кг/л", "Толщина, мм", "Ширина, мм", "Длина, м", "Ячейка X, мм", "Ячейка Y, мм", "Диаметр проволоки, мм", "Срок годности", "Место хранения"],
    )

    def number(value):
        return float(value) if value is not None else ""

    metal_lots = MaterialStockLot.objects.select_related("grade").filter(
        Q(profile_type="sheet", quantity_remaining__gt=0)
        | (~Q(profile_type="sheet") & Q(length_remaining_mm__gt=0))
    )
    if selected_order:
        metal_lots = metal_lots.filter(order=selected_order)
    grouped = OrderedDict()
    for lot in metal_lots:
        if lot.profile_type == "sheet":
            row = (
                lot.name, lot.grade.name, lot.thickness_mm, lot.width_mm,
                lot.piece_length_mm / MM_PER_METER if lot.piece_length_mm is not None else None, lot.storage_location,
            )
            key = ("sheet",) + row
            grouped.setdefault(key, {"row": row, "quantity": ZERO})["quantity"] += lot.quantity_remaining
            continue

        pieces = lot.quantity_remaining if lot.quantity_remaining > 0 else 1
        length_each = lot.length_remaining_mm / pieces / MM_PER_METER
        if lot.profile_type in {"round_pipe", "rect_tube"}:
            profile_label = "Труба круглая" if lot.profile_type == "round_pipe" else "Труба профильная"
            row = (
                lot.name, profile_label, lot.grade.name, lot.profile_name,
                lot.outer_diameter_mm, lot.width_mm, lot.height_mm, lot.wall_thickness_mm,
                length_each, lot.storage_location,
            )
            key = ("pipe",) + row
        else:
            export_labels = {
                "round_bar": "Круг", "square_bar": "Квадрат", "hex_bar": "Шестигранник",
                "rect_bar": "Полоса", "angle": "Уголок", "channel": "Швеллер",
                "beam": "Двутавр", "bulb_flat": "Полособульб", "other": "Другой",
            }
            row = (
                lot.name, export_labels.get(lot.profile_type, lot.get_profile_type_display()),
                lot.grade.name, lot.profile_name, lot.outer_diameter_mm, lot.width_mm,
                lot.height_mm, lot.thickness_mm, length_each, lot.kg_per_meter,
                lot.storage_location,
            )
            key = ("rolled",) + row
        grouped.setdefault(key, {"row": row, "quantity": ZERO})["quantity"] += pieces

    for key, item in grouped.items():
        kind = key[0]
        row = item["row"]
        quantity = item["quantity"]
        if kind == "sheet":
            sheet_page.append([row[0], row[1], number(row[2]), number(row[3]), number(row[4]), number(quantity), row[5]])
        elif kind == "pipe":
            pipe_page.append([row[0], row[1], row[2], row[3], number(row[4]), number(row[5]), number(row[6]), number(row[7]), number(row[8]), number(quantity), row[9]])
        else:
            rolled_page.append([row[0], row[1], row[2], row[3], number(row[4]), number(row[5]), number(row[6]), number(row[7]), number(row[8]), number(quantity), number(row[9]), row[10]])

    auxiliary_lots = AuxiliaryMaterialLot.objects.filter(quantity_remaining__gt=0)
    if selected_order:
        auxiliary_lots = auxiliary_lots.filter(order=selected_order)
    auxiliary_grouped = OrderedDict()
    for lot in auxiliary_lots:
        row = (
            lot.get_category_display(), lot.name, lot.brand, lot.characteristics,
            lot.get_unit_display(), lot.package_description, lot.density_kg_l,
            lot.thickness_mm, lot.width_mm,
            lot.length_mm / MM_PER_METER if lot.length_mm is not None else None,
            lot.mesh_cell_width_mm,
            lot.mesh_cell_height_mm, lot.wire_diameter_mm, lot.expiry_date,
            lot.storage_location,
        )
        auxiliary_grouped.setdefault(row, ZERO)
        auxiliary_grouped[row] += lot.quantity_remaining
    for row, quantity in auxiliary_grouped.items():
        auxiliary_page.append([
            row[0], row[1], row[2], row[3], row[4], number(quantity), row[5],
            number(row[6]), number(row[7]), number(row[8]), number(row[9]),
            number(row[10]), number(row[11]), number(row[12]), row[13], row[14],
        ])

    scope_name = selected_order.order_number if selected_order else "Весь физический склад"
    instruction = workbook.create_sheet("Инструкция", 0)
    instruction.append(["Фактический склад материалов"])
    instruction.append([f"Выгружено: {scope_name}"])
    instruction.append(["Измените фактические значения, удалите отсутствующие позиции или добавьте новые строки. Линейные длины указаны в метрах."])
    instruction.append(["Загрузите файл обратно с типом «Инвентаризация» и выберите тот же склад или проект."])
    instruction.append(["После подтверждения значения заменят текущие остатки без задвоения. История сохранится как корректировка."])
    instruction.column_dimensions["A"].width = 110
    instruction["A1"].font = openpyxl.styles.Font(bold=True, size=14, color="163A66")

    for sheet in workbook.worksheets:
        if sheet.title == "Инструкция":
            continue
        sheet.auto_filter.ref = f"A1:{sheet.cell(1, sheet.max_column).column_letter}{max(1, sheet.max_row)}"
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(
                30, max(13, max(len(str(cell.value or "")) for cell in column) + 2)
            )

    output = BytesIO()
    workbook.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="material_stock_actual.xlsx"'
    return response


@material_access_required
def material_requests(request):
    documents = MaterialRequest.objects.select_related("order", "requested_by").annotate(line_count=Count("lines"))
    status = request.GET.get("status", "")
    if status:
        documents = documents.filter(status=status)
    return render(request, "scanner/material_requests.html", {"documents": documents, "statuses": MaterialRequest.STATUS_CHOICES, "status": status})


@material_access_required
def material_request_detail(request, request_id):
    document = get_object_or_404(MaterialRequest.objects.select_related("order", "requested_by"), pk=request_id)
    if request.method == "POST":
        recipient = get_object_or_404(Employee, pk=request.POST.get("recipient_id"), is_active=True)
        issuer = get_object_or_404(WarehouseIssuer, pk=request.POST.get("issuer_id"), is_active=True)
        selected = request.POST.getlist("line_ids")
        values = {
            line.id: {
                "quantity": request.POST.get(f"quantity_{line.id}"),
                "length_mm": _millimeters_from_meters(request.POST.get(f"length_{line.id}")),
            }
            for line in document.lines.all() if str(line.id) in selected
        }
        try:
            batch_token, _ = issue_request_lines(
                document, values, recipient, request.POST.get("basis", ""), request.user, issuer.name
            )
            messages.success(request, "Материал выдан. Накладная сформирована.")
            return redirect("material_issue_print", batch_token=batch_token)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    lines = list(document.lines.select_related("requirement__grade", "requirement__assembly_ref__item"))
    for line in lines:
        line.available = available_for_requirement(line.requirement)
        line.length_requested_m = _meters(line.length_requested_mm)
        line.length_issued_m = _meters(line.length_issued_mm)
        line.length_remaining_m = _meters(line.length_remaining_mm)
    return render(request, "scanner/material_request_detail.html", {
        "document": document, "lines": lines,
        "employees": Employee.objects.filter(is_active=True).order_by("last_name", "first_name"),
        "warehouse_issuers": WarehouseIssuer.objects.filter(is_active=True),
    })


@material_access_required
@require_POST
def material_request_reject(request, request_id):
    reason = request.POST.get("rejection_reason", "").strip()
    if not reason:
        messages.error(request, "Укажите причину отклонения заявки.")
        return redirect("material_request_detail", request_id=request_id)
    with transaction.atomic():
        document = get_object_or_404(MaterialRequest.objects.select_for_update(), pk=request_id)
        if document.status != "open":
            messages.error(request, "Отклонить можно только открытую заявку без проведённой выдачи.")
            return redirect("material_request_detail", request_id=request_id)
        document.status = "cancelled"
        document.rejection_reason = reason
        document.rejected_by = request.user
        document.rejected_at = timezone.now()
        document.save(update_fields=["status", "rejection_reason", "rejected_by", "rejected_at"])
    messages.success(request, f"Заявка {document.number} отклонена.")
    return redirect("material_request_detail", request_id=document.id)


@material_access_required
def material_request_print(request, request_id):
    document = get_object_or_404(MaterialRequest.objects.select_related("order", "requested_by"), pk=request_id)
    lines = list(document.lines.select_related("requirement__grade", "requirement__assembly_ref__item"))
    for line in lines:
        line.length_requested_m = _meters(line.length_requested_mm)
    return render(request, "scanner/material_request_print.html", {"document": document, "lines": lines})


@material_access_required
def material_issue_log(request):
    transactions = MaterialTransaction.objects.filter(transaction_type="out").exclude(batch_token="").select_related(
        "stock_lot__grade", "recipient", "order", "created_by"
    ).order_by("-created_at")
    by_token = defaultdict(list)
    for item in transactions:
        item.length_m = _meters(item.length_mm)
        by_token[item.batch_token].append(item)
    groups = [{
        "token": token, "date": items[0].created_at, "recipient": items[0].recipient_name,
        "issuer_name": items[0].issuer_name,
        "basis": items[0].basis, "order": items[0].order, "created_by": items[0].created_by,
        "destination": (
            items[0].request_line.request.destination
            if items[0].request_line_id and items[0].request_line.request_id else ""
        ),
        "items": items, "mass": sum((item.mass_kg for item in items), ZERO),
    } for token, items in by_token.items()]
    return render(request, "scanner/material_issue_log.html", {"groups": groups})


@material_access_required
def material_issue_print(request, batch_token):
    items = list(MaterialTransaction.objects.filter(transaction_type="out", batch_token=batch_token).select_related(
        "stock_lot__grade", "request_line__request", "recipient", "order", "created_by"
    ))
    if not items:
        return redirect("material_issue_log")
    for item in items:
        item.length_m = _meters(item.length_mm)
    return render(request, "scanner/material_issue_print.html", {
        "items": items, "head": items[0], "total_mass": sum((item.mass_kg for item in items), ZERO),
        "number": f"МН-{items[0].created_at:%Y%m%d}-{items[0].id}",
        "issuer_name": items[0].issuer_name or str(items[0].created_by or ""),
    })
