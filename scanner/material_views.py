from collections import defaultdict
from io import BytesIO

import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from scanner.material_models import (
    MaterialGrade,
    MaterialRequirement,
    MaterialRequest,
    MaterialStockLot,
    MaterialTransaction,
    ZERO,
)
from scanner.material_services import (
    available_for_requirement,
    create_material_request,
    decimal_value,
    issue_request_lines,
    validate_material_geometry,
)
from scanner.models import Employee, Order, OrderItem


def _can_use_material_warehouse(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return hasattr(user, "employee") and user.employee.role in {"admin", "dispatcher", "storekeeper", "supervisor", "technologist"}


material_access_required = user_passes_test(_can_use_material_warehouse)


def _row_value(row, headers, aliases, default=None):
    for alias in aliases:
        for index, header in enumerate(headers):
            if alias in header:
                return row[index] if index < len(row) else default
    return default


@material_access_required
def material_home(request):
    q = request.GET.get("q", "").strip()
    orders = Order.objects.filter(material_requirements__isnull=False).distinct().order_by("-id")
    if q:
        orders = orders.filter(Q(order_number__icontains=q) | Q(full_name__icontains=q) | Q(project__icontains=q))
    rows = []
    for order in orders:
        requirements = order.material_requirements.all()
        rows.append({
            "order": order,
            "positions": requirements.count(),
            "mass": requirements.aggregate(total=Sum("calculated_mass_kg"))["total"] or ZERO,
            "requests": order.material_requests.count(),
        })
    return render(request, "scanner/material_home.html", {
        "rows": rows,
        "orders": Order.objects.order_by("-id")[:100],
        "q": q,
        "stock_mass": MaterialStockLot.objects.aggregate(total=Sum("mass_remaining_kg"))["total"] or ZERO,
        "open_requests": MaterialRequest.objects.filter(status__in=["open", "partial"]).count(),
    })


@material_access_required
@require_POST
def material_import(request):
    order = get_object_or_404(Order, pk=request.POST.get("order_id"))
    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, "Выберите файл Excel.")
        return redirect("material_home")
    try:
        workbook = openpyxl.load_workbook(upload, data_only=True)
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
            if data["assembly_name"]:
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
                "item_name": data["item_name"],
                "assembly_name": data["assembly_name"],
                "grade": data["grade"],
                "profile_type": data["profile_type"],
                "profile_name": data["profile_name"],
            }
            defaults = {key: value for key, value in data.items() if key not in lookup and key != "order"}
            MaterialRequirement.objects.update_or_create(**lookup, defaults=defaults)
    messages.success(request, f"Загружено позиций: {len(prepared)}. Площадь и масса рассчитаны автоматически.")
    return redirect("material_order_detail", order_id=order.id)


@material_access_required
def material_import_template(request):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Материалы"
    headers = [
        "Наименование", "Узел / подсборка", "Марка материала", "Тип профиля", "Сортамент",
        "Количество", "Длина единицы, мм", "Общая длина, мм", "Толщина, мм", "Ширина, мм",
        "Высота, мм", "Наружный диаметр, мм", "Стенка, мм", "Масса 1 м, кг", "Масса единицы, кг", "Примечание",
    ]
    sheet.append(headers)
    sheet.append(["Лист для корпуса", "Корпус", "Ст3", "Лист", "Лист 8", 2, 2000, "", 8, 1000, "", "", "", "", "", "Пример"])
    sheet.append(["Труба для рамы", "Рама", "Сталь", "Труба круглая", "Труба 57×3,5", 4, 6000, 24000, "", "", "", 57, 3.5, "", "", "Пример"])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:P{sheet.max_row}"
    for cell in sheet[1]:
        cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")
        cell.fill = openpyxl.styles.PatternFill("solid", fgColor="163A66")
        cell.alignment = openpyxl.styles.Alignment(wrap_text=True)
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = min(28, max(12, max(len(str(c.value or "")) for c in column) + 2))
    density = workbook.create_sheet("Справочник плотностей")
    density.append(["Марка материала", "Группа", "Плотность, кг/м³"])
    for grade in MaterialGrade.objects.filter(is_active=True):
        density.append([grade.name, grade.get_category_display(), float(grade.density_kg_m3)])
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
    return render(request, "scanner/material_order_detail.html", {"order": order, "requirements": requirements, "q": q})


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
                length_initial_mm=decimal_value(request.POST.get("length_mm")),
                thickness_mm=decimal_value(request.POST.get("thickness_mm"), None),
                width_mm=decimal_value(request.POST.get("width_mm"), None),
                height_mm=decimal_value(request.POST.get("height_mm"), None),
                outer_diameter_mm=decimal_value(request.POST.get("outer_diameter_mm"), None),
                wall_thickness_mm=decimal_value(request.POST.get("wall_thickness_mm"), None),
                piece_length_mm=decimal_value(request.POST.get("piece_length_mm"), None),
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
    lots = MaterialStockLot.objects.select_related("grade", "order").filter(Q(quantity_remaining__gt=0) | Q(length_remaining_mm__gt=0))
    if q:
        lots = lots.filter(Q(name__icontains=q) | Q(grade__name__icontains=q) | Q(profile_name__icontains=q) | Q(batch_number__icontains=q))
    return render(request, "scanner/material_stock.html", {
        "lots": lots, "q": q, "grades": MaterialGrade.objects.filter(is_active=True),
        "orders": Order.objects.order_by("-id")[:100], "profiles": MaterialRequirement.PROFILE_CHOICES,
    })


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
        selected = request.POST.getlist("line_ids")
        values = {line.id: {"quantity": request.POST.get(f"quantity_{line.id}"), "length_mm": request.POST.get(f"length_{line.id}")} for line in document.lines.all() if str(line.id) in selected}
        try:
            batch_token, _ = issue_request_lines(document, values, recipient, request.POST.get("basis", ""), request.user)
            messages.success(request, "Материал выдан. Накладная сформирована.")
            return redirect("material_issue_print", batch_token=batch_token)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    lines = list(document.lines.select_related("requirement__grade", "requirement__assembly_ref__item"))
    for line in lines:
        line.available = available_for_requirement(line.requirement)
    return render(request, "scanner/material_request_detail.html", {
        "document": document, "lines": lines,
        "employees": Employee.objects.filter(is_active=True).order_by("last_name", "first_name"),
    })


@material_access_required
def material_request_print(request, request_id):
    document = get_object_or_404(MaterialRequest.objects.select_related("order", "requested_by"), pk=request_id)
    return render(request, "scanner/material_request_print.html", {"document": document})


@material_access_required
def material_issue_log(request):
    transactions = MaterialTransaction.objects.filter(transaction_type="out").exclude(batch_token="").select_related(
        "stock_lot__grade", "recipient", "order", "created_by"
    ).order_by("-created_at")
    by_token = defaultdict(list)
    for item in transactions:
        by_token[item.batch_token].append(item)
    groups = [{
        "token": token, "date": items[0].created_at, "recipient": items[0].recipient_name,
        "basis": items[0].basis, "order": items[0].order, "created_by": items[0].created_by,
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
    return render(request, "scanner/material_issue_print.html", {
        "items": items, "head": items[0], "total_mass": sum((item.mass_kg for item in items), ZERO),
        "number": f"МН-{items[0].created_at:%Y%m%d}-{items[0].id}",
    })
