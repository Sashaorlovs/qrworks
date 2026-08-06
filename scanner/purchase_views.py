import json
import openpyxl
from datetime import datetime
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponse
import uuid
from django.db.models import Max
import uuid
import pytz
from django.db.models import Q, Sum
from scanner.purchase_models import PurchaseItem, PurchaseTransaction
from scanner.models import Order, OrderItem, Employee

@login_required
def purchase_list(request):
    items = PurchaseItem.objects.select_related('order', 'assembly_ref').all()
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    order_filter = request.GET.get('order', '').strip()
    
    if q:
        items = items.filter(Q(item_name__icontains=q) | Q(designation__icontains=q) | Q(order__order_number__icontains=q) | Q(assembly_name__icontains=q) | Q(order__items__item__name__icontains=q))
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
@require_POST
def purchase_import(request):
    file = request.FILES.get('file')
    if not file:
        return JsonResponse({'error': 'Файл не выбран'}, status=400)
    try:
        wb = openpyxl.load_workbook(file, data_only=True)
    except Exception as e:
        return JsonResponse({'error': f'Ошибка чтения: {e}'}, status=400)

    order_id = request.POST.get('order_id', '').strip()
    order = Order.objects.get(id=order_id) if order_id else None

    created = 0
    errors = []

    # Определяем лист спецификации и лист закупки по заголовкам
    spec_ws = None
    purch_ws = None
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        headers = [str(c.value).strip().lower() if c.value else '' for c in next(ws.iter_rows(min_row=1, max_row=1))]
        # Если есть "подсборка" или "требуемое" — это спецификация
        if any('подсборка' in h or 'сборка' in h for h in headers):
            spec_ws = ws
            spec_headers = headers
        elif any('закуп' in h or 'purchased' in h for h in headers) and not any('подсборка' in h for h in headers):
            purch_ws = ws
            purch_headers = headers
        elif any('требуемое' in h for h in headers):
            spec_ws = ws
            spec_headers = headers

    # Если не нашли спецификацию, берём активный лист как спецификацию
    if spec_ws is None:
        spec_ws = wb.active
        spec_headers = [str(c.value).strip().lower() if c.value else '' for c in next(spec_ws.iter_rows(min_row=1, max_row=1))]
    # Если не нашли закупку, но есть второй лист, берём его
    if purch_ws is None and len(wb.sheetnames) >= 2:
        purch_ws = wb[wb.sheetnames[1]] if wb.sheetnames[1] != spec_ws.title else wb[wb.sheetnames[0]]
        purch_headers = [str(c.value).strip().lower() if c.value else '' for c in next(purch_ws.iter_rows(min_row=1, max_row=1))]

    # Анализ листа закупки
    purchased_dict = {}
    if purch_ws:
        idx_name2 = next((i for i, h in enumerate(purch_headers) if 'наименование' in h), None)
        idx_qty2 = next((i for i, h in enumerate(purch_headers) if 'закуп' in h or 'purchased' in h), None)
        if idx_name2 is not None and idx_qty2 is not None:
            for row in purch_ws.iter_rows(min_row=2, values_only=True):
                if not row or all(c is None for c in row):
                    continue
                name = str(row[idx_name2]).strip() if row[idx_name2] else ''
                qty = int(row[idx_qty2]) if row[idx_qty2] else 0
                if name:
                    purchased_dict[name.lower()] = qty

    # Анализ листа спецификации
    idx_name1 = next((i for i, h in enumerate(spec_headers) if 'наименование' in h), None)
    idx_req = next((i for i, h in enumerate(spec_headers) if 'требуемое' in h or 'кол-во' in h), None)
    idx_asm = next((i for i, h in enumerate(spec_headers) if 'подсборка' in h or 'сборка' in h), None)
    idx_purch1 = next((i for i, h in enumerate(spec_headers) if 'закуп' in h or 'purchased' in h), None)

    if idx_name1 is None:
        errors.append('Не найден столбец "Наименование" в спецификации')
        return JsonResponse({'success': False, 'errors': errors})

    errors.append(f'Спецификация: {spec_headers}, индексы: name={idx_name1}, req={idx_req}, asm={idx_asm}')
    if purch_ws:
        errors.append(f'Закупка: {purch_headers}')

    # Чтение спецификации
    for row_idx, row in enumerate(spec_ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row or all(c is None for c in row):
            continue
        try:
            name = str(row[idx_name1]).strip() if row[idx_name1] else ''
            required = int(row[idx_req]) if idx_req is not None and row[idx_req] else 0
            assembly = str(row[idx_asm]).strip() if idx_asm is not None and row[idx_asm] else ''

            if not name:
                continue

            new_purchased = purchased_dict.get(name.lower())
            if new_purchased is None and idx_purch1 is not None:
                new_purchased = int(row[idx_purch1]) if row[idx_purch1] else 0
            if new_purchased is None:
                new_purchased = required

            existing = PurchaseItem.objects.filter(
                order=order,
                item_name__iexact=name,
                assembly_name__iexact=assembly
            ).first()

            if existing:
                existing.quantity_required += required
                if new_purchased:
                    existing.quantity_purchased = new_purchased
                existing.save()
                created += 1
            else:
                assembly_ref = None
                if assembly and order:
                    assembly_ref = OrderItem.objects.filter(order=order, item__name__icontains=assembly).first()
                PurchaseItem.objects.create(
                    order=order, assembly_ref=assembly_ref,
                    item_name=name, assembly_name=assembly,
                    quantity_required=required, quantity_purchased=new_purchased,
                    purchase_status='pending'
                )
                created += 1
        except Exception as e:
            errors.append(f'Строка {row_idx}: {e}')

    return JsonResponse({'success': True, 'created': created, 'errors': errors})

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
    
    items = PurchaseItem.objects.select_related('order').all()
    transactions = PurchaseTransaction.objects.filter(transaction_type='out', purchase_item__in=items)\
        .values('purchase_item__item_name').annotate(total=Sum('quantity'))
    
    issued_dict = {}
    for t in transactions:
        key = t['purchase_item__item_name'].strip().lower()
        issued_dict[key] = t['total']
    
    remains = defaultdict(lambda: {'purchased': 0, 'projects': set()})
    for item in items:
        key = item.item_name.strip().lower()
        if item.quantity_purchased > remains[key]['purchased']:
            remains[key]['purchased'] = item.quantity_purchased or 0
        if item.order:
            remains[key]['projects'].add(item.order.full_name or item.order.order_number)
    
    remains_list = []
    for name, data in remains.items():
        issued = issued_dict.get(name, 0)
        available = data['purchased'] - issued
        remains_list.append({
            'name': name,
            'projects': ', '.join(sorted(data['projects'])),
            'purchased': data['purchased'],
            'issued': issued,
            'available': available,
        })
    
    # Добавляем детализацию по проектам
    for item in remains_list:
        key = item['name']
        item['project_details'] = []
        proj_data = defaultdict(lambda: {'purchased': 0, 'issued': 0})
        for pi in items:
            if pi.item_name.strip().lower() == key:
                proj_name = pi.order.full_name or pi.order.order_number
                if pi.quantity_purchased > proj_data[proj_name]['purchased']:
                    proj_data[proj_name]['purchased'] = pi.quantity_purchased or 0
        for proj_name, data in proj_data.items():
            issued = PurchaseTransaction.objects.filter(
                purchase_item__item_name__iexact=key,
                purchase_item__order__full_name=proj_name if 'full_name' in dir(pi.order) else None,
                transaction_type='out'
            ).aggregate(s=Sum('quantity'))['s'] or 0
            # Упростим: issued по проекту не считаем отдельно, оставим общее
            proj_data[proj_name]['issued'] = 0
        # Пересчитаем issued по проектам
        for proj_name in proj_data:
            proj_issued = 0
            for pi in items:
                if pi.item_name.strip().lower() == key and (pi.order.full_name or pi.order.order_number) == proj_name:
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
        remains_list = [r for r in remains_list if q.lower() in r['name'].lower()]
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
    })


def purchase_issue(request):
    """Страница выдачи с выбором получателя из списка сотрудников"""
    from django.db.models import Sum
    from django.db.models import Sum
    items = PurchaseItem.objects.filter(purchase_status='ready_for_issue').select_related('order', 'assembly_ref').annotate(issued_qty=Sum('transactions__quantity', filter=Q(transactions__transaction_type='out')))\
        .annotate(issued_qty=Sum('transactions__quantity', filter=Q(transactions__transaction_type='out')))
    
    q = request.GET.get('q', '').strip()
    if q:
        q_lower = q.lower()
        items = [i for i in items if q_lower in (i.assembly_name or '').lower() or q_lower in (i.item_name or '').lower()]

    # Собираем общий остаток по каждому наименованию (как на странице остатков)
    from collections import defaultdict
    global_remains = defaultdict(lambda: {'purchased': 0, 'issued': 0})
    for item in items:
        key = item.item_name.strip().lower()
        # purchased берём максимальный среди всех позиций с этим названием
        if item.quantity_purchased > global_remains[key]['purchased']:
            global_remains[key]['purchased'] = item.quantity_purchased or 0
        # issued суммируем
        global_remains[key]['issued'] += item.issued_qty or 0

    # Вычисляем остаток для каждой позиции как общий остаток по наименованию
    for item in items:
        key = item.item_name.strip().lower()
        total_purchased = global_remains[key]['purchased']
        total_issued = global_remains[key]['issued']
        item.remaining = total_purchased - total_issued

    employees = Employee.objects.all().order_by('last_name', 'first_name')
    return render(request, 'scanner/purchase_issue.html', {
        'items': items,
        'q': q,
        'employees': employees,
    })


def purchase_bulk_issue(request):
    """Групповая выдача с накладной"""
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
    issued = []
    for d in items_data:
        try:
            item = PurchaseItem.objects.get(id=d['id'])
        except PurchaseItem.DoesNotExist:
            continue
        qty = int(d.get('quantity', 0))
        if qty <= 0:
            continue

        # Проверяем общий остаток по всем позициям с таким же наименованием
        from django.db.models import Sum
        # Общее закупленное (максимальное среди всех позиций с этим названием)
        max_purchased = PurchaseItem.objects.filter(
            item_name__iexact=item.item_name
        ).aggregate(m=Max('quantity_purchased'))['m'] or 0
        # Общее выданное (сумма по всем позициям с этим названием)
        total_issued = PurchaseTransaction.objects.filter(
            purchase_item__item_name__iexact=item.item_name,
            transaction_type='out'
        ).aggregate(s=Sum('quantity'))['s'] or 0
        
        available = max_purchased - total_issued
        qty = min(qty, available)
        if qty <= 0:
            continue
        PurchaseTransaction.objects.create(
            purchase_item=item, transaction_type='out', quantity=qty,
            recipient=str(recipient), basis=basis, batch_token=batch_token,
            created_by=request.user
        )
        item.purchase_status = 'issued'
        item.save()
        issued.append({'name': item.item_name, 'qty': qty, 'assembly': item.assembly_name})

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
    
    ws['A2'] = f'Дата: {datetime.now().strftime("%d.%m.%Y %H:%M")}'
    ws['A2'].font = Font(size=11)
    ws['A3'] = f'Кто выдал: {request.user.get_full_name() or request.user.username}'
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
    ws[f'A{row+1}'] = f'({request.user.get_full_name() or request.user.username})'
    ws[f'C{row+1}'] = f'({recipient})'
    for r in [row, row+1]:
        ws[f'A{r}'].font = Font(size=11)
        ws[f'C{r}'].font = Font(size=11)

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=issue_nakladnaya.xlsx'
    wb.save(response)
    return response

@login_required
def purchase_reprint_nakladnaya(request, transaction_id):
    """Повторная печать накладной по batch_token"""
    from openpyxl.styles import Font, Alignment, Border, Side
    batch = request.GET.get('batch', '')
    
    if batch:
        transactions = PurchaseTransaction.objects.filter(batch_token=batch).select_related('purchase_item', 'created_by')
        if not transactions.exists():
            return HttpResponse('Накладная не найдена', status=404)
        first = transactions.first()
    else:
        t = get_object_or_404(PurchaseTransaction, id=transaction_id)
        # Если у этой транзакции есть batch_token, собираем все транзакции с ним
        if t.batch_token:
            transactions = PurchaseTransaction.objects.filter(batch_token=t.batch_token).select_related('purchase_item', 'created_by')
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
    ws['A2'] = f'Дата: {first.created_at.astimezone(msk).strftime("%d.%m.%Y %H:%M")}'
    ws['A3'] = f'Кто выдал: {first.created_by.get_full_name() if first.created_by else "—"}'
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
        ws[f'C{row}'] = t.purchase_item.assembly_name or '—'
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
    response['Content-Disposition'] = f'attachment; filename=nakladnaya_{batch or first.id}.xlsx'
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
            key = item.item_name.strip().lower()
            groups[key].append(item)
    
    # Если задан поиск, скрываем группы, не соответствующие запросу
    if q:
        filtered_groups = {}
        for key, group_items in groups.items():
            if any(q.lower() in (it.assembly_name or '').lower() or q.lower() in (it.item_name or '').lower() for it in group_items):
                filtered_groups[key] = group_items
        groups = filtered_groups
    
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
    items = PurchaseItem.objects.select_related('order', 'assembly_ref').all()
    q = request.GET.get('q', '').strip()
    order_filter = request.GET.get('order', '').strip()
    if q:
        items = items.filter(Q(item_name__icontains=q) | Q(assembly_name__icontains=q) | Q(order__order_number__icontains=q))
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
    
    items_qs = PurchaseItem.objects.select_related('order', 'assembly_ref')\
        .annotate(issued_qty=Sum('transactions__quantity', filter=Q(transactions__transaction_type='out')))
    
    # Группировка: purchased = max, issued = sum
    grouped = defaultdict(lambda: {'purchased': 0, 'issued': 0, 'projects': defaultdict(lambda: {'purchased': 0, 'issued': 0})})
    for i in items_qs:
        key = i.item_name.strip().lower()
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
        q_lower = q.lower()
        grouped = {k: v for k, v in grouped.items() if q_lower in k}
    
    result_items = []
    for name, data in grouped.items():
        data['name'] = name[0].upper() + name[1:] if name else name
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
        data['ids'] = list(PurchaseItem.objects.filter(item_name__iexact=name).values_list('id', flat=True))
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
    """Групповая выдача со страницы остатков (поддержка групп)"""
    import uuid
    from django.db.models import Sum, Max
    data = json.loads(request.body)
    names = data.get('names', [])
    recipient_id = data.get('recipient_id', '').strip()
    basis = data.get('basis', '').strip()
    quantities = data.get('quantities', {})

    if not names or not recipient_id:
        return JsonResponse({'error': 'Не выбраны позиции или получатель'}, status=400)
    try:
        recipient = Employee.objects.get(id=recipient_id)
    except Employee.DoesNotExist:
        return JsonResponse({'error': 'Получатель не найден'}, status=400)

    # Фильтрация в Python (регистронезависимо)
    all_items = PurchaseItem.objects.all()
    matched = []
    for item in all_items:
        item_lower = item.item_name.lower()
        for name in names:
            name_lower = name.lower()
            if item_lower == name_lower or item_lower.startswith(name_lower):
                matched.append(item)
                break
    if not matched:
        return JsonResponse({'error': 'Позиции не найдены'}, status=404)

    # Группируем по уникальным именам для выдачи
    issued = []
    batch_token = str(uuid.uuid4())
    for name in names:
        qty = quantities.get(name, 0)
        if qty <= 0:
            continue
        name_lower = name.lower()
        # Считаем общий доступный остаток по всем позициям, начинающимся с name_lower
        relevant = [it for it in all_items if it.item_name.lower().startswith(name_lower)]
        if not relevant:
            continue
        total_purchased = sum(it.quantity_purchased or 0 for it in relevant)
        total_issued = PurchaseTransaction.objects.filter(
            purchase_item__in=relevant,
            transaction_type='out'
        ).aggregate(s=Sum('quantity'))['s'] or 0
        available = total_purchased - total_issued
        if available <= 0:
            continue
        qty = min(qty, available)
        target = relevant[0]
        PurchaseTransaction.objects.create(
            purchase_item=target, transaction_type='out', quantity=qty,
            recipient=str(recipient), basis=basis, batch_token=batch_token,
            created_by=request.user
        )
        issued.append({'name': target.item_name, 'qty': qty, 'assembly': target.assembly_name or ''})

    if not issued:
        return JsonResponse({'error': 'Нет доступных остатков для выдачи'}, status=400)

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
    ws['A2'] = f'Дата: {datetime.now().strftime("%d.%m.%Y %H:%M")}'
    ws['A3'] = f'Кто выдал: {request.user.get_full_name() or request.user.username}'
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
    ws[f'A{row+1}'] = f'({request.user.get_full_name() or request.user.username})'; ws[f'C{row+1}'] = f'({recipient})'
    for r in [row, row+1]:
        ws[f'A{r}'].font = Font(size=11); ws[f'C{r}'].font = Font(size=11)

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=remains_issue_nakladnaya.xlsx'
    wb.save(response)
    return response

def purchase_issue_log(request):
    """Журнал выдач с группировкой по batch_token и детализацией"""
    from django.core.paginator import Paginator
    from django.db.models import Count, Sum, Min
    # Группируем по batch_token (если batch_token пустой, используем id как уникальный)
    grouped = PurchaseTransaction.objects.filter(transaction_type='out')        .values('batch_token', 'recipient', 'basis', 'created_by__username', 'created_by__first_name', 'created_by__last_name')        .annotate(
            total_qty=Sum('quantity'),
            items_count=Count('id'),
            first_date=Min('created_at'),
            first_id=Min('id')
        )        .order_by('-first_date')
    
    # Получаем детализацию для каждой группы
    batch_tokens = [g['batch_token'] for g in grouped if g['batch_token']]
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
    
    paginator = Paginator(grouped, 20)
    page_number = request.GET.get('page', 1)
    transactions = paginator.get_page(page_number)
    
    # Добавляем имя создавшего и детали
    for t in transactions:
        t['created_by_name'] = (t['created_by__last_name'] or '') + ' ' + (t['created_by__first_name'] or '') or t['created_by__username'] or '—'
        t['created_at'] = t['first_date']
        t['details'] = details.get(t['batch_token'], [])
    
    return render(request, 'scanner/purchase_issue_log.html', {'transactions': transactions})
