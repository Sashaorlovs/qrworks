import json
import openpyxl
from datetime import datetime
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.urls import reverse
import uuid
from django.db.models import Max
import uuid
import pytz
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.core.exceptions import ValidationError
from django.utils import timezone
from scanner.purchase_models import (
    PurchaseItem,
    PurchaseTransaction,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchasePreparation,
)
from scanner.purchase_documents import (
    assign_invoice_number,
    create_purchase_request,
    general_surplus_state,
    issue_general_surplus,
    issue_purchase_request,
)
from scanner.purchase_allocation import (
    PurchaseAllocationError,
    allocate_purchase_items,
    allocation_state,
)
from scanner.models import Order, OrderItem, Employee
from scanner.material_models import WarehouseIssuer
from scanner.purchase_normalization import normalize_purchase_name


def _user_display_name(user):
    if not user:
        return '—'
    employee = getattr(user, 'employee', None)
    if employee:
        return str(employee).strip()
    return user.get_full_name().strip() or user.username


def _purchase_search_matches(query, *values):
    terms = normalize_purchase_name(query).split()
    haystack = normalize_purchase_name(' '.join(str(value or '') for value in values))
    return all(term in haystack for term in terms)


def _is_purchase_administrator(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    employee = getattr(user, 'employee', None)
    return bool(employee and employee.role == 'admin')

@login_required
def purchase_list(request):
    items = PurchaseItem.objects.select_related('order', 'assembly_ref').all()
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    order_filter = request.GET.get('order', '').strip()
    
    if q:
        for term in normalize_purchase_name(q).split():
            items = items.filter(
                Q(normalized_name__icontains=term) | Q(designation__icontains=term)
                | Q(order__order_number__icontains=term) | Q(assembly_name__icontains=term)
                | Q(order__items__item__name__icontains=term)
            )
    if status:
        items = items.filter(purchase_status=status)
    if order_filter:
        items = items.filter(order_id=order_filter)
    

    items = list(items[:500])

    # Добавляем main_assembly через отдельный запрос
    order_ids = [i.order_id for i in items if i.order_id]
    main_items = {}
    if order_ids:
        for oi in OrderItem.objects.filter(order_id__in=order_ids, parent__isnull=True).select_related('item'):
            main_items[oi.order_id] = oi.item.name if oi.item else '—'

    for item in items:
        item.main_assembly = main_items.get(item.order_id, '—')
        item.remaining = (item.quantity_purchased or 0) - (item.issued_qty or 0)


    orders = Order.objects.all().order_by('-id')[:50]
    for o in orders:
        first = o.items.first()
        o.display_name = o.full_name if o.full_name else o.order_number

    return render(request, 'scanner/purchase_list.html', {
        'items': items,
        'statuses': PurchaseItem.PURCHASE_STATUS_CHOICES,
        'orders': orders,
        'q': q,
        'status': status,
        'order_filter': order_filter,
    })

@login_required
def purchase_import(request):
    from scanner.purchase_import import enrich_purchase_preview, parse_purchase_workbook, save_purchase_preview

    preview, errors, warnings, token = None, [], [], ""
    selected_order_id = request.POST.get("order_id", "") if request.method == "POST" else ""
    selected_preparation_id = request.POST.get("preparation_id", "") if request.method == "POST" else ""
    new_preparation_name = request.POST.get("preparation_name", "").strip() if request.method == "POST" else ""
    if request.method == "POST":
        action = request.POST.get("action", "preview")
        if action == "confirm":
            token = request.POST.get("token", "")
            payload = request.session.get(f"purchase_import:{token}")
            if not payload:
                messages.error(request, "Проверка устарела. Загрузите файл ещё раз.")
                return redirect("purchase_import")
            order = Order.objects.filter(pk=payload.get("order_id")).first() if payload.get("order_id") else None
            preparation = None
            if payload.get("preparation_id"):
                preparation = get_object_or_404(PurchasePreparation, pk=payload["preparation_id"])
            elif payload.get("preparation_name"):
                preparation, _ = PurchasePreparation.objects.get_or_create(
                    name=payload["preparation_name"], defaults={"created_by": request.user}
                )
            saved = save_purchase_preview(order, payload["rows"], preparation)
            request.session.pop(f"purchase_import:{token}", None)
            messages.success(request, f"Спецификация проверена и загружена. Позиций: {saved}.")
            if order:
                return redirect("purchase_spec_detail", spec_id=order.id)
            return redirect("purchase_preparation_detail", preparation_id=preparation.id)

        upload = request.FILES.get("file")
        targets = bool(selected_order_id) + bool(selected_preparation_id) + bool(new_preparation_name)
        if targets != 1:
            errors.append("Выберите один вариант: проект, существующую предварительную ведомость или название новой ведомости.")
        if not upload:
            errors.append("Выберите файл Excel.")
        if not errors:
            order = get_object_or_404(Order, pk=selected_order_id) if selected_order_id else None
            preparation = get_object_or_404(PurchasePreparation, pk=selected_preparation_id) if selected_preparation_id else None
            try:
                workbook = openpyxl.load_workbook(upload, data_only=True)
                preview, errors, warnings = parse_purchase_workbook(workbook, order, preparation)
                if preview and not errors:
                    preview = enrich_purchase_preview(preview, order, preparation)
            except Exception as exc:
                errors.append(f"Не удалось прочитать файл: {exc}")
            if preview and not errors:
                token = uuid.uuid4().hex
                session_rows = []
                for row in preview:
                    session_row = {
                        key: format(value, "f") if isinstance(value, Decimal) else value
                        for key, value in row.items()
                    }
                    session_rows.append(session_row)
                request.session[f"purchase_import:{token}"] = {
                    "order_id": order.id if order else None,
                    "preparation_id": preparation.id if preparation else None,
                    "preparation_name": new_preparation_name,
                    "rows": session_rows,
                }
                request.session.modified = True

    preview_summary = None
    if preview and not errors:
        unique_groups = {}
        for row in preview:
            unique_groups[row["normalized_name"]] = row
        preview_summary = {
            "add": sum(row["action_code"] == "add" for row in preview),
            "update": sum(row["action_code"] == "update" for row in preview),
            "same": sum(row["action_code"] == "same" for row in preview),
            "deficit_groups": sum(row["deficit_after"] > 0 for row in unique_groups.values()),
            "deficit_total": sum((row["deficit_after"] for row in unique_groups.values()), Decimal("0")),
        }

    return render(request, "scanner/purchase_import.html", {
        "orders": Order.objects.order_by("-id")[:100],
        "preparations": PurchasePreparation.objects.filter(assigned_order__isnull=True),
        "selected_order_id": str(selected_order_id),
        "selected_preparation_id": str(selected_preparation_id),
        "new_preparation_name": new_preparation_name,
        "preview": preview, "preview_summary": preview_summary,
        "errors": errors, "warnings": warnings, "token": token,
    })


@login_required
def purchase_import_template(request):
    from openpyxl.styles import Alignment, Font, PatternFill

    workbook = openpyxl.Workbook()
    instruction = workbook.active
    instruction.title = "Инструкция"
    instructions = [
        "Загрузка спецификации стандартных и покупных изделий",
        "1. Проект выбирается на странице загрузки; в Excel его указывать не нужно.",
        "2. В «Спецификации» каждая строка относится к конкретному узлу или подсборке.",
        "3. В «Закупке» указывается общее закупленное количество по наименованию для проекта.",
        "4. Перед записью система покажет все позиции, ошибки и совпавшие варианты наименований.",
        "5. Лишняя точка, двойной пробел, разные тире, регистр и символы ×/х/x не создают отдельный крепёж.",
        "6. Повторная загрузка обновляет количество и не задваивает потребность.",
    ]
    for text in instructions:
        instruction.append([text])
    instruction.column_dimensions["A"].width = 115
    instruction["A1"].font = Font(bold=True, size=14, color="163A66")

    def make_sheet(title, headers, rows):
        sheet = workbook.create_sheet(title)
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{sheet.cell(1, len(headers)).column_letter}{sheet.max_row}"
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="163A66")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(55, max(16, max(len(str(cell.value or "")) for cell in column) + 2))

    make_sheet("Спецификация", ["Наименование", "Обозначение / артикул", "Требуемое количество", "Узел / подсборка"], [
        ["Болт М10-6gx25.88.0118 ГОСТ 7798-70", "", 2, "Полумуфта"],
        ["Маслёнка 1.1.Ц18.хр (М6х1) ГОСТ 19853-74", "", 1, "Полумуфта"],
    ])
    make_sheet("Закупка", ["Наименование", "Закуплено"], [
        ["Болт М10-6gx25.88.0118 ГОСТ 7798-70", 100],
        ["Маслёнка 1.1.Ц18.хр (М6х1) ГОСТ 19853-74", 50],
    ])
    output = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    output["Content-Disposition"] = 'attachment; filename="purchase_import_template.xlsx"'
    workbook.save(output)
    return output

def purchase_change_status(request):
    ids = request.POST.getlist('ids[]')
    new_status = request.POST.get('status', '').strip()
    valid_statuses = [s[0] for s in PurchaseItem.PURCHASE_STATUS_CHOICES]
    if new_status not in valid_statuses:
        return JsonResponse({'error': 'Неверный статус'}, status=400)
    if not ids:
        return JsonResponse({'error': 'Ничего не выбрано'}, status=400)
    PurchaseItem.objects.filter(id__in=ids).update(purchase_status=new_status)
    return JsonResponse({'success': True, 'count': len(ids)})

@login_required
def purchase_remains(request):
    from django.db.models import Sum
    from collections import defaultdict
    
    items = PurchaseItem.objects.select_related('order').filter(order__isnull=False)
    transactions = PurchaseTransaction.objects.filter(transaction_type='out', purchase_item__in=items)\
        .values('purchase_item__normalized_name').annotate(total=Sum('quantity'))
    
    issued_dict = {}
    for t in transactions:
        key = t['purchase_item__normalized_name']
        issued_dict[key] = t['total']
    
    remains = defaultdict(lambda: {'name': '', 'purchased': 0, 'projects': set()})
    for item in items:
        key = item.normalized_name
        if not remains[key]['name']:
            remains[key]['name'] = item.item_name
        if item.quantity_purchased > remains[key]['purchased']:
            remains[key]['purchased'] = item.quantity_purchased or 0
        if item.order:
            remains[key]['projects'].add(item.order.full_name or item.order.order_number)
    
    remains_list = []
    for key, data in remains.items():
        issued = issued_dict.get(key, 0)
        available = data['purchased'] - issued
        remains_list.append({
            'key': key,
            'name': data['name'],
            'projects': ', '.join(sorted(data['projects'])),
            'purchased': data['purchased'],
            'issued': issued,
            'available': available,
        })
    
    # Добавляем детализацию по проектам
    for item in remains_list:
        key = item['key']
        item['project_details'] = []
        proj_data = defaultdict(lambda: {'purchased': 0, 'issued': 0})
        for pi in items:
            if pi.normalized_name == key:
                proj_name = pi.order.full_name or pi.order.order_number
                if pi.quantity_purchased > proj_data[proj_name]['purchased']:
                    proj_data[proj_name]['purchased'] = pi.quantity_purchased or 0
        for proj_name, data in proj_data.items():
            proj_data[proj_name]['issued'] = 0
        # Пересчитаем issued по проектам
        for proj_name in proj_data:
            proj_issued = 0
            for pi in items:
                if pi.normalized_name == key and (pi.order.full_name or pi.order.order_number) == proj_name:
                    proj_issued += PurchaseTransaction.objects.filter(purchase_item=pi, transaction_type='out').aggregate(s=Sum('quantity'))['s'] or 0
            proj_data[proj_name]['issued'] = proj_issued
            proj_data[proj_name]['available'] = proj_data[proj_name]['purchased'] - proj_issued
            item['project_details'].append({
                'project': proj_name,
                'purchased': proj_data[proj_name]['purchased'],
                'issued': proj_data[proj_name]['issued'],
                'available': proj_data[proj_name]['available'],
            })
        item['project_details'].sort(key=lambda x: x['project'])
    
    # Пересчитываем итоги как сумму по проектам
    for item in remains_list:
        item['purchased'] = sum(pd['purchased'] for pd in item['project_details'])
        item['issued'] = sum(pd['issued'] for pd in item['project_details'])
        item['available'] = item['purchased'] - item['issued']
        item['destinations'] = []
        for requirement in items:
            if requirement.normalized_name != item['key']:
                continue
            if requirement.purchase_status != 'ready_for_issue':
                continue
            state = allocation_state(requirement)
            if state.allocatable <= 0:
                continue
            order_name = 'Без заказа'
            if requirement.order:
                order_name = requirement.order.full_name or requirement.order.order_number
            item['destinations'].append({
                'id': requirement.id,
                'order': order_name,
                'assembly': requirement.assembly_name or 'Без подсборки',
                'required': state.required_for_assembly,
                'issued': state.issued_for_assembly,
                'available': state.allocatable,
            })
        item['destinations'].sort(key=lambda row: (row['order'], row['assembly']))
        item['general_options'] = []
        seen_orders = set()
        for requirement in items:
            if (
                requirement.normalized_name != item['key']
                or not requirement.order_id
                or requirement.purchase_status in ('pending', 'awaiting_payment', 'paid', 'shipped')
            ):
                continue
            if requirement.order_id in seen_orders:
                continue
            seen_orders.add(requirement.order_id)
            surplus = general_surplus_state(requirement)
            if surplus['general_available'] <= 0:
                continue
            item['general_options'].append({
                'item_id': requirement.id,
                'order': requirement.order.full_name or requirement.order.order_number,
                'available': surplus['general_available'],
                'reserved': surplus['reserved'],
            })
        item['general_available'] = sum(row['available'] for row in item['general_options'])
    
    # Фильтры для отображения
    q = request.GET.get('q', '').strip()
    group = request.GET.get('group', '').strip()
    
    # Список групп (первые слова)
    all_groups = set()
    for item in remains_list:
        if item['name']:
            first_word = item['name'].strip().split()[0].lower()
            all_groups.add(first_word)
    groups = sorted(all_groups)
    
    if q:
        remains_list = [r for r in remains_list if _purchase_search_matches(q, r['name'])]
    if group:
        remains_list = [r for r in remains_list if r['name'].strip().lower().startswith(group.lower())]
    
    remains_list.sort(key=lambda x: x['name'])
    employees = Employee.objects.all().order_by('last_name', 'first_name')
    return render(request, 'scanner/purchase_remains.html', {
        'remains': remains_list,
        'q': q,
        'group': group,
        'groups': groups,
        'employees': employees,
        'warehouse_issuers': WarehouseIssuer.objects.filter(is_active=True),
    })


@login_required
@require_POST
def purchase_general_issue(request):
    role = getattr(getattr(request.user, 'employee', None), 'role', '')
    if not request.user.is_staff and role not in {'admin', 'purchase_storekeeper', 'technologist'}:
        return JsonResponse({'error': 'Недостаточно прав для выдачи.'}, status=403)
    try:
        data = json.loads(request.body)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Некорректные данные.'}, status=400)
    recipient = get_object_or_404(
        Employee, pk=data.get('recipient_id'), is_active=True
    )
    issuer = get_object_or_404(WarehouseIssuer, pk=data.get('issuer_id'), is_active=True)
    try:
        batch_token, number, _ = issue_general_surplus(
            data.get('item_id'),
            data.get('quantity'),
            recipient,
            data.get('basis', ''),
            request.user,
            issuer,
        )
    except (ValidationError, PurchaseItem.DoesNotExist) as exc:
        message = ' '.join(exc.messages) if isinstance(exc, ValidationError) else 'Позиция не найдена.'
        return JsonResponse({'error': message}, status=400)
    return JsonResponse({
        'success': True,
        'number': number,
        'print_url': reverse('purchase_invoice_print', args=[batch_token]),
    })


def purchase_issue(request):
    """Страница выдачи с выбором получателя из списка сотрудников"""
    items = list(
        PurchaseItem.objects.filter(purchase_status='ready_for_issue', order__isnull=False)
        .select_related('order', 'assembly_ref')
        .order_by('order_id', 'item_name', 'assembly_name')
    )
    
    q = request.GET.get('q', '').strip()
    if q:
        items = [i for i in items if _purchase_search_matches(q, i.item_name, i.assembly_name)]

    available_items = []
    for item in items:
        state = allocation_state(item)
        item.remaining = state.allocatable
        item.issued_for_assembly = state.issued_for_assembly
        if item.remaining > 0:
            available_items.append(item)
    items = available_items

    employees = Employee.objects.all().order_by('last_name', 'first_name')
    return render(request, 'scanner/purchase_issue.html', {
        'items': items,
        'q': q,
        'employees': employees,
    })


def purchase_bulk_issue(request):
    """Групповая выдача с накладной"""
    return JsonResponse(
        {'error': 'Прямая выдача отключена. Сначала сформируйте онлайн-заявку.'},
        status=400,
    )

    # Legacy direct-issue implementation retained below for rollback compatibility.
    data = json.loads(request.body)
    items_data = data.get('items', [])
    # ОТЛАДКА: возвращаем полученные данные обратно, чтобы увидеть их в консоли браузера
    if request.GET.get('debug') == '1':
        return JsonResponse({'debug_data': data, 'items_data': items_data})
    recipient_id = data.get('recipient_id', '').strip()
    basis = data.get('basis', '').strip()

    if not items_data or not recipient_id:
        return JsonResponse({'error': 'Нет данных'}, status=400)

    try:
        recipient = Employee.objects.get(id=recipient_id)
    except Employee.DoesNotExist:
        return JsonResponse({'error': 'Получатель не найден'}, status=400)

    batch_token = str(uuid.uuid4())
    try:
        movements = allocate_purchase_items(
            lines=items_data,
            recipient=recipient,
            basis=basis,
            batch_token=batch_token,
            created_by=request.user,
        )
    except PurchaseAllocationError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    document_number = assign_invoice_number(movements)

    issued = [
        {
            'name': movement.purchase_item.item_name,
            'qty': movement.quantity,
            'assembly': movement.purchase_item.assembly_name,
        }
        for movement in movements
    ]

    # Excel-накладная
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Накладная выдачи"
    
    # Стили
    bold_font = Font(bold=True, size=12)
    header_font = Font(bold=True, size=11)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    wrap_align = Alignment(wrap_text=True, vertical='center')
    center_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    
    # Заголовок
    ws.merge_cells('A1:C1')
    ws['A1'] = 'НАКЛАДНАЯ НА ВЫДАЧУ СТАНДАРТНЫХ ИЗДЕЛИЙ И КРЕПЕЖА'
    ws['A1'].font = Font(bold=True, size=14)
    ws['A1'].alignment = center_align
    
    ws['A2'] = f'№ {document_number} от {datetime.now().strftime("%d.%m.%Y %H:%M")}'
    ws['A2'].font = Font(size=11)
    ws['A3'] = f'Кто выдал: {_user_display_name(request.user)}'
    ws['A4'] = f'Кому выдано: {recipient}'
    ws['A5'] = f'Основание: {basis}'
    # Добавляем главную сборку (из первого элемента заказа)
    main_assembly = ''
    if issued:
        first_item = PurchaseItem.objects.filter(id=items_data[0]['id']).select_related('order').first()
        if first_item and first_item.order:
            first_order_item = first_item.order.items.first()
            if first_order_item and first_order_item.item:
                main_assembly = first_order_item.item.name
    ws['A6'] = f'Главная сборка: {main_assembly or "—"}'
    
    # Таблица
    ws['A7'] = 'Наименование'
    ws['B7'] = 'Количество'
    ws['C7'] = 'Подсборка'
    for col in ['A', 'B', 'C']:
        cell = ws[f'{col}7']
        cell.font = header_font
        cell.border = thin_border
        cell.alignment = center_align
    
    row = 8
    for it in issued:
        ws[f'A{row}'] = it['name']
        ws[f'B{row}'] = it['qty']
        ws[f'C{row}'] = it['assembly']
        for col in ['A', 'B', 'C']:
            cell = ws[f'{col}{row}']
            cell.border = thin_border
            cell.alignment = wrap_align if col == 'A' else center_align
        row += 1
    
    # Ширина столбцов

    ws.column_dimensions['A'].width = 50
    ws.column_dimensions['B'].width = 12
    ws.column_dimensions['C'].width = 35
    
    # Подписи
    row += 1
    ws[f'A{row}'] = 'Выдал: _________________________'
    ws[f'C{row}'] = 'Получил: _________________________'
    ws[f'A{row+1}'] = f'({_user_display_name(request.user)})'
    ws[f'C{row+1}'] = f'({recipient})'
    for r in [row, row+1]:
        ws[f'A{r}'].font = Font(size=11)
        ws[f'C{r}'].font = Font(size=11)

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename={document_number}.xlsx'
    wb.save(response)
    return response

@login_required
def purchase_reprint_nakladnaya(request, transaction_id):
    """Повторная печать накладной по batch_token"""
    from openpyxl.styles import Font, Alignment, Border, Side
    batch = request.GET.get('batch', '')
    
    if batch:
        transactions = PurchaseTransaction.objects.filter(batch_token=batch).select_related('purchase_item', 'created_by__employee')
        if not transactions.exists():
            return HttpResponse('Накладная не найдена', status=404)
        first = transactions.first()
    else:
        t = get_object_or_404(PurchaseTransaction, id=transaction_id)
        # Если у этой транзакции есть batch_token, собираем все транзакции с ним
        if t.batch_token:
            transactions = PurchaseTransaction.objects.filter(batch_token=t.batch_token).select_related('purchase_item', 'created_by__employee')
            first = transactions.first()
        else:
            transactions = [t]
            first = t
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Накладная выдачи"
    
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'),
                         top=Side(style='thin'), bottom=Side(style='thin'))
    center_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    wrap_align = Alignment(wrap_text=True, vertical='center')
    
    ws.merge_cells('A1:C1')
    ws['A1'] = 'НАКЛАДНАЯ НА ВЫДАЧУ СТАНДАРТНЫХ ИЗДЕЛИЙ И КРЕПЕЖА'
    ws['A1'].font = Font(bold=True, size=14)
    ws['A1'].alignment = center_align
    
    msk = pytz.timezone('Europe/Moscow')
    document_number = first.document_number or f'НК-{first.created_at.astimezone(msk):%Y}-{first.id:06d}'
    ws['A2'] = f'№ {document_number} от {first.created_at.astimezone(msk).strftime("%d.%m.%Y %H:%M")}'
    ws['A3'] = f'Кто выдал: {first.issuer_name or _user_display_name(first.created_by)}'
    ws['A4'] = f'Кому выдано: {first.recipient}'
    ws['A5'] = f'Основание: {first.basis or "—"}'
    
    ws['A7'] = 'Наименование'
    ws['B7'] = 'Количество'
    ws['C7'] = 'Подсборка'
    for col in ['A', 'B', 'C']:
        ws[f'{col}7'].font = Font(bold=True)
        ws[f'{col}7'].border = thin_border
        ws[f'{col}7'].alignment = center_align
    
    row = 8
    for t in transactions:
        ws[f'A{row}'] = t.purchase_item.item_name
        ws[f'B{row}'] = t.quantity
        ws[f'C{row}'] = 'Общепроизводственные нужды' if t.is_general_use else (t.purchase_item.assembly_name or '—')
        for col in ['A', 'B', 'C']:
            ws[f'{col}{row}'].border = thin_border
            ws[f'{col}{row}'].alignment = wrap_align if col == 'A' else center_align
        row += 1
    

    ws.column_dimensions['A'].width = 50
    ws.column_dimensions['B'].width = 12
    ws.column_dimensions['C'].width = 35
    
    row += 1
    ws[f'A{row}'] = 'Выдал: _________________________'
    ws[f'C{row}'] = 'Получил: _________________________'
    
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename={document_number}.xlsx'
    wb.save(response)
    return response

def check_purchase_access(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if request.user.is_staff:
            return view_func(request, *args, **kwargs)
        if hasattr(request.user, 'employee'):
            role = request.user.employee.role
            if role in ['admin', 'purchase_storekeeper']:
                return view_func(request, *args, **kwargs)
            if role == 'supervisor' and request.method == 'GET':
                return view_func(request, *args, **kwargs)
            if role == 'technologist':
                return view_func(request, *args, **kwargs)
        messages.error(request, 'Доступ запрещён')
        return redirect('home')
    return wrapper

@login_required
@check_purchase_access
def purchase_spec_list(request):
    """Список спецификаций (заказов) с покупными изделиями"""
    if request.method == 'POST':
        preparation_name = request.POST.get('preparation_name', '').strip()
        if not preparation_name:
            messages.error(request, 'Укажите название предварительной ведомости.')
        elif PurchasePreparation.objects.filter(name__iexact=preparation_name).exists():
            messages.error(request, 'Предварительная ведомость с таким названием уже существует.')
        else:
            preparation = PurchasePreparation.objects.create(name=preparation_name, created_by=request.user)
            messages.success(request, 'Предварительная ведомость создана. Теперь добавьте позиции вручную или загрузите Excel.')
            return redirect('purchase_preparation_detail', preparation_id=preparation.id)
        return redirect('purchase_list')

    order_ids = list(set(PurchaseItem.objects.filter(order__isnull=False).values_list('order_id', flat=True)))
    orders = Order.objects.filter(id__in=order_ids).order_by('-id')[:50]
    
    for o in orders:
        first = o.items.first()
        o.display_name = o.full_name if o.full_name else o.order_number
        o.purchase_count = PurchaseItem.objects.filter(order=o).count()
        o.issued_count = PurchaseItem.objects.filter(order=o, purchase_status='issued').count()
        if o.purchase_count == 0:
            o.status_label = 'Нет позиций'
        elif o.issued_count == o.purchase_count:
            o.status_label = 'Выдан полностью'
        elif o.issued_count > 0:
            o.status_label = 'Выдан частично'
        else:
            o.status_label = 'Ожидает выдачи'
    
    all_orders = Order.objects.all().order_by('-id')[:50]
    for o in all_orders:
        first = o.items.first()
        o.display_name = o.full_name if o.full_name else o.order_number

    return render(request, 'scanner/purchase_list.html', {
        'orders': orders,
        'all_orders': all_orders,
        'positions_total': PurchaseItem.objects.filter(order__isnull=False).count(),
        'ready_total': PurchaseItem.objects.filter(purchase_status='ready_for_issue', order__isnull=False).count(),
        'open_requests': PurchaseRequest.objects.filter(status__in=['open', 'partial']).count(),
        'invoice_total': PurchaseTransaction.objects.filter(transaction_type='out').exclude(batch_token='').values('batch_token').distinct().count(),
        'preparations': PurchasePreparation.objects.filter(assigned_order__isnull=True).annotate(item_count=Count('items')),
        'can_manage_purchase_statements': _is_purchase_administrator(request.user),
    })


@login_required
def purchase_statement_manage(request):
    """Administrator-only cleanup screen outside the Django admin."""
    if not _is_purchase_administrator(request.user):
        return HttpResponseForbidden('Управление ведомостями доступно только администратору.')

    preparations = PurchasePreparation.objects.filter(assigned_order__isnull=True).annotate(
        item_count=Count('items'),
    )
    order_ids = PurchaseItem.objects.filter(order__isnull=False).values_list('order_id', flat=True).distinct()
    orders = list(Order.objects.filter(id__in=order_ids).order_by('-id'))
    for order in orders:
        order.purchase_item_count = PurchaseItem.objects.filter(order=order).count()
        order.purchase_request_count = PurchaseRequest.objects.filter(order=order).count()
        order.purchase_movement_count = PurchaseTransaction.objects.filter(purchase_item__order=order).count()
        order.purchase_delete_blocked = order.purchase_movement_count > 0
    return render(request, 'scanner/purchase_statement_manage.html', {
        'preparations': preparations,
        'orders': orders,
    })


@login_required
@require_POST
@transaction.atomic
def purchase_statement_delete(request, statement_type, object_id):
    if not _is_purchase_administrator(request.user):
        return HttpResponseForbidden('Удаление ведомостей доступно только администратору.')
    if request.POST.get('confirm') != 'yes':
        messages.error(request, 'Удаление не подтверждено.')
        return redirect('purchase_statement_manage')

    if statement_type == 'preparation':
        preparation = get_object_or_404(
            PurchasePreparation.objects.select_for_update(),
            pk=object_id,
            assigned_order__isnull=True,
        )
        items = preparation.items.select_for_update().filter(order__isnull=True)
        if PurchaseTransaction.objects.filter(purchase_item__in=items).exists() or PurchaseRequestLine.objects.filter(purchase_item__in=items).exists():
            messages.error(request, 'Ведомость связана с заявками или движениями и не может быть удалена.')
            return redirect('purchase_statement_manage')
        item_count = items.count()
        name = preparation.name
        items.delete()
        preparation.delete()
        messages.success(request, f'Предварительная ведомость «{name}» удалена. Позиций: {item_count}.')
        return redirect('purchase_statement_manage')

    if statement_type == 'order':
        order = get_object_or_404(Order.objects.select_for_update(), pk=object_id)
        items = PurchaseItem.objects.select_for_update().filter(order=order)
        if PurchaseTransaction.objects.filter(purchase_item__in=items).exists():
            messages.error(request, 'Удаление запрещено: по ведомости уже есть складские движения или накладные.')
            return redirect('purchase_statement_manage')
        item_count = items.count()
        request_count = PurchaseRequest.objects.filter(order=order).count()
        PurchaseRequest.objects.filter(order=order).delete()
        items.delete()
        PurchasePreparation.objects.filter(assigned_order=order).delete()
        messages.success(
            request,
            f'Ведомость заказа {order.order_number} удалена. Позиций: {item_count}, заявок: {request_count}. Сам заказ сохранён.',
        )
        return redirect('purchase_statement_manage')

    return HttpResponse('Неизвестный тип ведомости.', status=400)


@login_required
@check_purchase_access
@transaction.atomic
def purchase_preparation_detail(request, preparation_id):
    from scanner.purchase_import import _quantity, enrich_purchase_preview

    preparation = get_object_or_404(PurchasePreparation, pk=preparation_id)
    binding_order = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action in {'add', 'save'}:
            item = None
            if action == 'save':
                item = get_object_or_404(PurchaseItem, pk=request.POST.get('item_id'), preparation=preparation)
            try:
                item_name = request.POST.get('item_name', '').strip()
                if not item_name:
                    raise ValidationError('Укажите наименование.')
                required = _quantity(request.POST.get('quantity_required'), 'Требуется')
                purchased = _quantity(request.POST.get('quantity_purchased'), 'Закуплено')
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))
            else:
                if item is None:
                    item = PurchaseItem(preparation=preparation, purchase_status='pending')
                item.item_name = item_name
                item.designation = request.POST.get('designation', '').strip()
                item.assembly_name = request.POST.get('assembly_name', '').strip()
                item.quantity_required = required
                item.quantity_purchased = purchased
                status = request.POST.get('purchase_status', item.purchase_status)
                if status in dict(PurchaseItem.PURCHASE_STATUS_CHOICES):
                    item.purchase_status = status
                item.save()
                messages.success(request, 'Позиция сохранена.')
            return redirect('purchase_preparation_detail', preparation_id=preparation.id)

        if action == 'preview_bind':
            binding_order = get_object_or_404(Order, pk=request.POST.get('order_id'))

        if action == 'bind':
            order = get_object_or_404(Order, pk=request.POST.get('order_id'))
            items = list(preparation.items.select_for_update().filter(order__isnull=True))
            conflicts = []
            for item in items:
                if PurchaseItem.objects.filter(
                    order=order, normalized_name=item.normalized_name,
                    assembly_name__iexact=item.assembly_name,
                ).exclude(pk=item.pk).exists():
                    conflicts.append(item.item_name)
            if conflicts:
                messages.error(request, 'Привязка остановлена: в заказе уже есть совпадающие позиции: ' + ', '.join(conflicts[:10]))
            else:
                for item in items:
                    item.order = order
                    item.assembly_ref = (
                        OrderItem.objects.filter(order=order, item__name__icontains=item.assembly_name).first()
                        if item.assembly_name else None
                    )
                    item.save(update_fields=['order', 'assembly_ref'])
                preparation.assigned_order = order
                preparation.assigned_at = timezone.now()
                preparation.save(update_fields=['assigned_order', 'assigned_at'])
                messages.success(request, f'Ведомость привязана к заказу {order.order_number}.')
                return redirect('purchase_spec_detail', spec_id=order.id)

    items = list(preparation.items.order_by('item_name', 'assembly_name', 'id'))
    draft_rows = [{
        'item_name': item.item_name,
        'normalized_name': item.normalized_name,
        'designation': item.designation,
        'assembly_name': item.assembly_name,
        'quantity_required': item.quantity_required,
        'quantity_purchased': item.quantity_purchased,
        'source_rows': [],
    } for item in items]
    draft_preview = enrich_purchase_preview(draft_rows, preparation=preparation) if draft_rows else []
    preview_by_id = {row['existing_id']: row for row in draft_preview}
    for item in items:
        item.preview_state = preview_by_id.get(item.id)
    unique_groups = {}
    for row in draft_preview:
        unique_groups[row['normalized_name']] = row
    preparation_summary = {
        'groups': len(unique_groups),
        'deficit_groups': sum(row['deficit_after'] > 0 for row in unique_groups.values()),
        'deficit_total': sum((row['deficit_after'] for row in unique_groups.values()), Decimal('0')),
    }
    binding_preview = []
    binding_conflicts = []
    binding_summary = None
    if binding_order and draft_rows:
        binding_preview = enrich_purchase_preview(draft_rows, order=binding_order)
        binding_conflicts = [row for row in binding_preview if row['existing_id']]
        binding_groups = {}
        for row in binding_preview:
            binding_groups[row['normalized_name']] = row
        binding_summary = {
            'deficit_groups': sum(row['deficit_after'] > 0 for row in binding_groups.values()),
            'deficit_total': sum((row['deficit_after'] for row in binding_groups.values()), Decimal('0')),
        }
    return render(request, 'scanner/purchase_preparation_detail.html', {
        'preparation': preparation,
        'items': items,
        'orders': Order.objects.order_by('-id')[:100],
        'statuses': PurchaseItem.PURCHASE_STATUS_CHOICES,
        'preparation_summary': preparation_summary,
        'binding_order': binding_order,
        'binding_preview': binding_preview,
        'binding_conflicts': binding_conflicts,
        'binding_summary': binding_summary,
    })


@login_required
@check_purchase_access
def purchase_spec_detail(request, spec_id):
    """Детальная страница спецификации покупных изделий"""
    from collections import defaultdict
    from django.db.models import Sum
    
    order = Order.objects.get(id=spec_id) if spec_id else None
    
    # Сохранение примечания (журнал)
    if request.method == 'POST' and 'save_notes' in request.POST:
        new_text = request.POST.get('notes', '').strip()
        if new_text:
            timestamp = datetime.now().strftime('%d.%m.%Y %H:%M')
            author = request.user.get_full_name() or request.user.username
            entry = f'[{timestamp}] {author}: {new_text}'
            
            current_notes = PurchaseItem.objects.filter(order_id=spec_id).values_list('notes', flat=True).first() or ''
            updated_notes = entry + '\n' + current_notes if current_notes else entry
            
            PurchaseItem.objects.filter(order_id=spec_id).update(notes=updated_notes)
            messages.success(request, 'Примечание добавлено')
        return redirect('purchase_spec_detail', spec_id=spec_id)
    
    # Очистка журнала примечаний
    if request.method == 'POST' and 'clear_notes' in request.POST:
        PurchaseItem.objects.filter(order_id=spec_id).update(notes='')
        messages.success(request, 'Журнал примечаний очищен')
        return redirect('purchase_spec_detail', spec_id=spec_id)
    
    # Все позиции заказа (без фильтрации)
    items = PurchaseItem.objects.filter(order_id=spec_id).select_related('order', 'assembly_ref')\
        .annotate(issued_qty=Sum('transactions__quantity', filter=Q(transactions__transaction_type='out')))\
        .order_by('item_name')
    
    q = request.GET.get('q', '').strip()
    view_mode = request.GET.get('view', 'name')
    
    # Группировка по всем позициям заказа
    groups = defaultdict(list)
    if view_mode == 'assembly':
        for item in items:
            key = item.assembly_name.strip().lower() if item.assembly_name else '—'
            groups[key].append(item)
    else:
        for item in items:
            key = item.normalized_name
            groups[key].append(item)
    
    # Если задан поиск, скрываем группы, не соответствующие запросу
    if q:
        groups = {
            key: group_items for key, group_items in groups.items()
            if any(_purchase_search_matches(q, it.item_name, it.assembly_name) for it in group_items)
        }
    
    # Формируем grouped_items
    grouped_items = []
    for key, group_items in groups.items():
        if view_mode == 'assembly':
            g = {
                'item_name': group_items[0].assembly_name or '—',
                'quantity_required': sum(i.quantity_required or 0 for i in group_items),
                'quantity_purchased': 0,
                'issued_qty': sum(i.issued_qty or 0 for i in group_items),
                'remaining': 0,
                'ids': [i.id for i in group_items],
                'statuses': set(i.purchase_status for i in group_items),
                'sub_items': [{
                    'id': i.id,
                    'assembly_name': i.item_name,
                    'quantity_required': i.quantity_required or 0,
                    'purchase_status': dict(PurchaseItem.PURCHASE_STATUS_CHOICES).get(i.purchase_status, i.purchase_status),
                    'status_raw': i.purchase_status,
                } for i in group_items],
            }
            g['purchase_status'] = list(g['statuses'])[0] if len(g['statuses']) == 1 else 'mixed'
            grouped_items.append(g)
        else:
            first = group_items[0]
            g = {
                'item_name': first.item_name,
                'quantity_required': sum(i.quantity_required or 0 for i in group_items),
                'quantity_purchased': group_items[0].quantity_purchased or 0,
                'issued_qty': sum(i.issued_qty or 0 for i in group_items),
                'remaining': (group_items[0].quantity_purchased or 0) - sum(i.issued_qty or 0 for i in group_items),
                'ids': [i.id for i in group_items],
                'statuses': set(i.purchase_status for i in group_items),
                'sub_items': [{
                    'id': i.id,
                    'assembly_name': i.assembly_name or '—',
                    'quantity_required': i.quantity_required or 0,
                    'purchase_status': i.get_purchase_status_display(),
                    'status_raw': i.purchase_status,
                } for i in group_items],
            }
            g['purchase_status'] = list(g['statuses'])[0] if len(g['statuses']) == 1 else 'mixed'
            grouped_items.append(g)
    
    general_notes = PurchaseItem.objects.filter(order_id=spec_id).values_list('notes', flat=True).first() or ''
    
    return render(request, 'scanner/purchase_spec_detail.html', {
        'order': order,
        'items': grouped_items,
        'statuses': PurchaseItem.PURCHASE_STATUS_CHOICES,
        'q': q,
        'general_notes': general_notes,
        'view_mode': view_mode,
    })

def purchase_export_request(request):
    """Экспорт заявки по текущему фильтру в Excel"""
    from openpyxl.styles import Font, Alignment, Border, Side
    items = PurchaseItem.objects.select_related('order', 'assembly_ref').filter(order__isnull=False)
    q = request.GET.get('q', '').strip()
    order_filter = request.GET.get('order', '').strip()
    if q:
        for term in normalize_purchase_name(q).split():
            items = items.filter(
                Q(normalized_name__icontains=term) | Q(assembly_name__icontains=term)
                | Q(order__order_number__icontains=term)
            )
    if order_filter:
        items = items.filter(order_id=order_filter)
    items = items.order_by('assembly_name', 'item_name')
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Заявка на склад"
    
    # Стили
    bold = Font(bold=True, size=12)
    title_font = Font(bold=True, size=14)
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    wrap_align = Alignment(wrap_text=True, vertical='center')
    
    # Заголовок
    ws.merge_cells('A1:C1')
    ws['A1'] = 'ЗАЯВКА НА СКЛАД СТАНДАРТНЫХ ИЗДЕЛИЙ'
    ws['A1'].font = title_font
    ws['A1'].alignment = center
    
    ws['A2'] = f'Дата: {datetime.now().strftime("%d.%m.%Y %H:%M")}'
    ws['A3'] = f'Сформировал: {request.user.get_full_name() or request.user.username}'
    # Добавляем наименование проекта
    project_name = 'не указан'
    if order_filter:
        try:
            proj_order = Order.objects.get(id=order_filter)
            project_name = proj_order.full_name or proj_order.order_number
        except:
            pass
    ws['A4'] = f'Проект: {project_name}'
    
    # Заголовки таблицы начинаются со строки 5 (сразу после "Сформировал" и "Проект")
    row_start = 5
    
    # Заголовки таблицы
    ws[f'A{row_start}'] = 'Наименование'
    ws[f'B{row_start}'] = 'Требуемое кол-во'
    ws[f'C{row_start}'] = 'Подсборка'
    for col in ['A', 'B', 'C']:
        ws[f'{col}{row_start}'].font = bold
        ws[f'{col}{row_start}'].border = thin_border
        ws[f'{col}{row_start}'].alignment = center
    
    # Данные — каждая подсборка отдельной строкой
    row = row_start + 1
    for item in items:
        ws[f'A{row}'] = item.item_name
        ws[f'B{row}'] = item.quantity_required
        ws[f'C{row}'] = item.assembly_name or '—'
        for col in ['A', 'B', 'C']:
            ws[f'{col}{row}'].border = thin_border
            ws[f'{col}{row}'].alignment = wrap_align if col != 'B' else center
        row += 1
    
    row += 2
    ws[f'A{row}'] = 'Запросил: _________________________'
    ws[f'C{row}'] = 'Дата: _______________'
    row += 3
    ws[f'A{row}'] = 'Скомплектовал: _________________________'
    ws[f'C{row}'] = 'Дата: _______________'

    ws.column_dimensions['A'].width = 50
    ws.column_dimensions['B'].width = 18
    ws.column_dimensions['C'].width = 35
    
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=zayavka_sklad.xlsx'
    wb.save(response)
    return response

@login_required
@check_purchase_access
def purchase_issue_remains(request):
    """Упрощённая выдача со страницы остатков (без привязки к проекту)"""
    from django.db.models import Sum
    from collections import defaultdict
    
    items_qs = PurchaseItem.objects.filter(order__isnull=False).select_related('order', 'assembly_ref')\
        .annotate(issued_qty=Sum('transactions__quantity', filter=Q(transactions__transaction_type='out')))
    
    # Группировка: purchased = max, issued = sum
    grouped = defaultdict(lambda: {'purchased': 0, 'issued': 0, 'projects': defaultdict(lambda: {'purchased': 0, 'issued': 0})})
    for i in items_qs:
        key = i.normalized_name
        # Общий purchased = максимум
        if i.quantity_purchased > grouped[key]['purchased']:
            grouped[key]['purchased'] = i.quantity_purchased or 0
        # Общий issued = сумма
        grouped[key]['issued'] += i.issued_qty or 0
        
        proj_name = i.order.full_name or i.order.order_number if i.order else 'Без проекта'
        # Для проекта purchased тоже максимум
        if i.quantity_purchased > grouped[key]['projects'][proj_name]['purchased']:
            grouped[key]['projects'][proj_name]['purchased'] = i.quantity_purchased or 0
        grouped[key]['projects'][proj_name]['issued'] += i.issued_qty or 0
    
    q = request.GET.get('q', '').strip()
    if q:
        grouped = {k: v for k, v in grouped.items() if _purchase_search_matches(q, k)}
    
    result_items = []
    for name, data in grouped.items():
        first_matching = next((item for item in items_qs if item.normalized_name == name), None)
        data['name'] = first_matching.item_name if first_matching else name
        data['projects_list'] = ', '.join(data['projects'].keys())
        # Детализация по проектам
        project_details = []
        for proj_name, pd in data['projects'].items():
            project_details.append({
                'name': proj_name,
                'purchased': pd['purchased'],
                'issued': pd['issued'],
                'remaining': pd['purchased'] - pd['issued']
            })
        data['project_details'] = project_details
        # Итоги: purchased = максимум по проектам
        data['purchased'] = max((pd['purchased'] for pd in project_details), default=0)
        data['issued'] = sum(pd['issued'] for pd in project_details)
        data['remaining'] = data['purchased'] - data['issued']
        data['ids'] = list(PurchaseItem.objects.filter(normalized_name=name, order__isnull=False).values_list('id', flat=True))
        result_items.append(data)
    
    employees = Employee.objects.all().order_by('last_name', 'first_name')
    
    return render(request, 'scanner/purchase_issue_remains.html', {
        'items': result_items,
        'q': q,
        'employees': employees,
    })

@login_required
@check_purchase_access
def purchase_remains_issue(request):
    """Выдача со страницы остатков на точные заказ и подсборку."""
    return JsonResponse(
        {'error': 'Выдача из остатков отключена. Сначала сформируйте онлайн-заявку.'},
        status=400,
    )

    # Legacy direct-issue implementation retained below for rollback compatibility.
    import uuid
    data = json.loads(request.body)
    allocations = data.get('allocations', [])
    recipient_id = data.get('recipient_id', '').strip()
    basis = data.get('basis', '').strip()

    if not allocations or not recipient_id:
        return JsonResponse({'error': 'Не выбраны позиции или получатель'}, status=400)
    try:
        recipient = Employee.objects.get(id=recipient_id)
    except Employee.DoesNotExist:
        return JsonResponse({'error': 'Получатель не найден'}, status=400)

    batch_token = str(uuid.uuid4())
    try:
        movements = allocate_purchase_items(
            lines=allocations,
            recipient=recipient,
            basis=basis,
            batch_token=batch_token,
            created_by=request.user,
        )
    except PurchaseAllocationError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    document_number = assign_invoice_number(movements)

    issued = [
        {
            'name': movement.purchase_item.item_name,
            'qty': movement.quantity,
            'assembly': movement.purchase_item.assembly_name or '',
        }
        for movement in movements
    ]

    # Excel-накладная
    from openpyxl.styles import Font, Alignment, Border, Side
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Накладная выдачи"
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
    center_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    wrap_align = Alignment(wrap_text=True, vertical='center')
    ws.merge_cells('A1:C1')
    ws['A1'] = 'НАКЛАДНАЯ НА ВЫДАЧУ СТАНДАРТНЫХ ИЗДЕЛИЙ И КРЕПЕЖА'
    ws['A1'].font = Font(bold=True, size=14)
    ws['A1'].alignment = center_align
    ws['A2'] = f'№ {document_number} от {datetime.now().strftime("%d.%m.%Y %H:%M")}'
    ws['A3'] = f'Кто выдал: {_user_display_name(request.user)}'
    ws['A4'] = f'Кому выдано: {recipient}'
    ws['A5'] = f'Основание: {basis}'
    ws['A7'] = 'Наименование'; ws['B7'] = 'Количество'; ws['C7'] = 'Подсборка'
    for col in ['A','B','C']:
        ws[f'{col}7'].font = Font(bold=True, size=11)
        ws[f'{col}7'].border = thin_border
        ws[f'{col}7'].alignment = center_align
    row = 8
    for it in issued:
        ws[f'A{row}'] = it['name']; ws[f'B{row}'] = it['qty']; ws[f'C{row}'] = it['assembly']
        for col in ['A','B','C']:
            ws[f'{col}{row}'].border = thin_border
            ws[f'{col}{row}'].alignment = wrap_align if col == 'A' else center_align
        row += 1
    ws.column_dimensions['A'].width = 50; ws.column_dimensions['B'].width = 12; ws.column_dimensions['C'].width = 35
    row += 1
    ws[f'A{row}'] = 'Выдал: _________________________'; ws[f'C{row}'] = 'Получил: _________________________'
    ws[f'A{row+1}'] = f'({_user_display_name(request.user)})'; ws[f'C{row+1}'] = f'({recipient})'
    for r in [row, row+1]:
        ws[f'A{r}'].font = Font(size=11); ws[f'C{r}'].font = Font(size=11)

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename={document_number}.xlsx'
    wb.save(response)
    return response

def purchase_issue_log(request):
    """Журнал выдач с группировкой по batch_token и детализацией"""
    from django.db.models import Count, Sum, Min
    q = request.GET.get('q', '').strip()

    grouped_qs = PurchaseTransaction.objects.filter(transaction_type='out')\
        .values('batch_token', 'document_number', 'recipient', 'basis', 'issuer_name', 'created_by__username', 'created_by__first_name', 'created_by__last_name', 'created_by__employee__last_name', 'created_by__employee__first_name', 'created_by__employee__middle_name')\
        .annotate(
            total_qty=Sum('quantity'),
            items_count=Count('id'),
            first_date=Min('created_at'),
            first_id=Min('id')
        )\
        .order_by('-first_date')

    # Получаем детализацию для каждой группы
    batch_tokens = [g['batch_token'] for g in grouped_qs if g['batch_token']]
    details = {}
    if batch_tokens:
        detail_qs = PurchaseTransaction.objects.filter(
            batch_token__in=batch_tokens, transaction_type='out'
        ).select_related('purchase_item').values('batch_token', 'purchase_item__item_name').annotate(qty=Sum('quantity'))
        for d in detail_qs:
            token = d['batch_token']
            if token not in details:
                details[token] = []
            details[token].append({'name': d['purchase_item__item_name'], 'qty': d['qty']})

    # Добавляем имя создавшего и детали
    for g in grouped_qs:
        employee_name = ' '.join(filter(None, [g['created_by__employee__last_name'], g['created_by__employee__first_name'], g['created_by__employee__middle_name']]))
        account_name = ' '.join(filter(None, [g['created_by__last_name'], g['created_by__first_name']]))
        g['created_by_name'] = g['issuer_name'] or employee_name or account_name or g['created_by__username'] or '—'
        g['created_at'] = g['first_date']
        g['details'] = details.get(g['batch_token'], [])
        g['display_number'] = g['document_number'] or f"НК-{g['first_date']:%Y}-{g['first_id']:06d}"

    # Фильтрация по q (получатель, основание, кто выдал)
    if q:
        q_lower = q.lower()
        filtered = []
        for g in grouped_qs:
            if (q_lower in (g['recipient'] or '').lower() or
                q_lower in (g['basis'] or '').lower() or
                q_lower in (g['created_by_name'] or '').lower()):
                filtered.append(g)
        transactions = filtered
    else:
        transactions = list(grouped_qs)

    return render(request, 'scanner/purchase_issue_log.html', {
        'transactions': transactions,
        'q': q,
    })


@login_required
@check_purchase_access
def purchase_requests(request):
    documents = PurchaseRequest.objects.select_related(
        'order', 'requested_by', 'requested_by__employee'
    ).annotate(
        line_count=Count('lines')
    )
    status = request.GET.get('status', '').strip()
    if status:
        documents = documents.filter(status=status)
    documents = list(documents)
    for document in documents:
        document.requested_by_name = _user_display_name(document.requested_by)
    return render(request, 'scanner/purchase_requests.html', {
        'documents': documents,
        'statuses': PurchaseRequest.STATUS_CHOICES,
        'status': status,
    })


@login_required
@check_purchase_access
@require_POST
def purchase_create_request(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    raw_ids = request.POST.getlist('item_ids')
    item_ids = []
    for value in raw_ids:
        for item_id in value.split(','):
            if item_id.strip().isdigit():
                item_ids.append(int(item_id.strip()))
    try:
        document = create_purchase_request(
            order, item_ids, request.user, request.POST.get('purpose', '')
        )
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('purchase_spec_detail', spec_id=order.id)
    messages.success(request, f'Заявка {document.number} сформирована.')
    return redirect('purchase_request_detail', request_id=document.id)


@login_required
@check_purchase_access
def purchase_request_detail(request, request_id):
    document = get_object_or_404(
        PurchaseRequest.objects.select_related('order', 'requested_by', 'cancelled_by', 'cancelled_by__employee'), pk=request_id
    )
    if request.method == 'POST':
        recipient = get_object_or_404(
            Employee, pk=request.POST.get('recipient_id'), is_active=True
        )
        issuer = get_object_or_404(WarehouseIssuer, pk=request.POST.get('issuer_id'), is_active=True)
        quantities = {
            int(line_id): request.POST.get(f'quantity_{line_id}', '0')
            for line_id in request.POST.getlist('line_ids')
            if line_id.isdigit()
        }
        try:
            batch_token, number, _ = issue_purchase_request(
                document,
                quantities,
                recipient,
                request.POST.get('basis', ''),
                request.user,
                issuer,
            )
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            messages.success(request, f'Выдача выполнена. Накладная {number} сформирована.')
            return redirect('purchase_invoice_print', batch_token=batch_token)

    lines = list(document.lines.select_related('purchase_item__order'))
    for line in lines:
        state = allocation_state(line.purchase_item)
        line.available_now = (
            min(state.allocatable, line.quantity_remaining)
            if line.purchase_item.purchase_status == 'ready_for_issue'
            else 0
        )
    employees = Employee.objects.filter(is_active=True).order_by('last_name', 'first_name')
    document.cancelled_by_name = _user_display_name(document.cancelled_by) if document.cancelled_by else ''
    return render(request, 'scanner/purchase_request_detail.html', {
        'document': document,
        'lines': lines,
        'employees': employees,
        'warehouse_issuers': WarehouseIssuer.objects.filter(is_active=True),
    })


@login_required
@check_purchase_access
@require_POST
def purchase_request_cancel(request, request_id):
    document = get_object_or_404(PurchaseRequest, pk=request_id)
    if document.status not in {'open', 'partial'}:
        messages.error(request, 'Отменить можно только открытую или частично выданную заявку.')
        return redirect('purchase_request_detail', request_id=document.id)
    reason = request.POST.get('cancellation_reason', '').strip()
    if not reason:
        messages.error(request, 'Укажите причину отмены заявки.')
        return redirect('purchase_request_detail', request_id=document.id)
    document.status = 'cancelled'
    document.cancellation_reason = reason
    document.cancelled_by = request.user
    document.cancelled_at = timezone.now()
    document.save(update_fields=['status', 'cancellation_reason', 'cancelled_by', 'cancelled_at'])
    messages.success(request, f'Заявка {document.number} отменена.')
    return redirect('purchase_request_detail', request_id=document.id)


@login_required
@check_purchase_access
def purchase_request_print(request, request_id):
    document = get_object_or_404(
        PurchaseRequest.objects.select_related('order', 'requested_by').prefetch_related(
            'lines__purchase_item'
        ),
        pk=request_id,
    )
    return render(request, 'scanner/purchase_request_print.html', {
        'document': document,
        'requested_by_name': _user_display_name(document.requested_by),
    })


@login_required
@check_purchase_access
def purchase_invoice_print(request, batch_token):
    items = list(
        PurchaseTransaction.objects.filter(
            batch_token=batch_token, transaction_type='out'
        ).select_related('purchase_item__order', 'created_by', 'request_line__request')
    )
    if not items:
        messages.error(request, 'Накладная не найдена.')
        return redirect('purchase_issue_log')
    head = items[0]
    number = head.document_number or f'НК-{head.created_at:%Y}-{head.id:06d}'
    order = head.purchase_item.order
    return render(request, 'scanner/purchase_invoice_print.html', {
        'items': items,
        'head': head,
        'number': number,
        'order': order,
        'total_quantity': sum(item.quantity for item in items),
        'issuer_name': head.issuer_name or _user_display_name(head.created_by),
    })
