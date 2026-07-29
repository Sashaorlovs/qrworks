import json
import openpyxl
from datetime import datetime
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponse
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
    if q:
        items = items.filter(Q(item_name__icontains=q) | Q(designation__icontains=q) | Q(order__order_number__icontains=q) | Q(assembly_name__icontains=q) | Q(order__items__item__name__icontains=q))
    if status:
        items = items.filter(purchase_status=status)
    
    from django.db.models import Sum
    items = items.annotate(issued_qty=Sum('transactions__quantity', filter=Q(transactions__transaction_type='out')))
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
        o.display_name = first.item_name if first and hasattr(first, 'item_name') else (first.item.name if first and first.item else '-')

    return render(request, 'scanner/purchase_list.html', {
        'items': items,
        'statuses': PurchaseItem.PURCHASE_STATUS_CHOICES,
        'orders': orders,
        'q': q,
        'status': status,
    })

@login_required
@require_POST
def purchase_import(request):
    file = request.FILES.get('file')
    if not file:
        return JsonResponse({'error': 'Файл не выбран'}, status=400)
    try:
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active
    except Exception as e:
        return JsonResponse({'error': f'Ошибка чтения: {e}'}, status=400)

    order_id = request.POST.get('order_id', '').strip()
    order = Order.objects.get(id=order_id) if order_id else None

    created = 0
    errors = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row or all(c is None for c in row):
            continue
        try:
            name = str(row[0]).strip() if row[0] else ''
            required = int(row[1]) if len(row) > 1 and row[1] else 0
            purchased = int(row[2]) if len(row) > 2 and row[2] else 0
            assembly = str(row[3]).strip() if len(row) > 3 and row[3] else ''
            if not name:
                continue

            assembly_ref = None
            if assembly and order:
                assembly_ref = OrderItem.objects.filter(order=order, item__name__icontains=assembly).first()

            PurchaseItem.objects.create(
                order=order, assembly_ref=assembly_ref,
                item_name=name, assembly_name=assembly,
                quantity_required=required, quantity_purchased=purchased,
                purchase_status='pending'
            )
            created += 1
        except Exception as e:
            errors.append(f'Строка {row_idx}: {e}')

    return JsonResponse({'success': True, 'created': created, 'errors': errors})

@login_required
@require_POST
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
    items = PurchaseItem.objects.all()
    remains = {}
    for item in items:
        key = item.item_name
        if key not in remains:
            remains[key] = {'name': item.item_name, 'received': 0, 'issued': 0}
    for t in PurchaseTransaction.objects.filter(purchase_item__in=items):
        key = t.purchase_item.item_name
        if key not in remains:
            continue
        if t.transaction_type == 'in':
            remains[key]['received'] += t.quantity
        else:
            remains[key]['issued'] += t.quantity
    remains_list = [{'name': d['name'], 'received': d['received'], 'issued': d['issued'],
                     'available': d['received'] - d['issued']}
                    for d in remains.values() if d['received'] > 0]
    remains_list.sort(key=lambda x: x['name'])
    return render(request, 'scanner/purchase_remains.html', {'remains': remains_list})

@login_required
def purchase_issue(request):
    """Страница выдачи с выбором получателя из списка сотрудников"""
    items = PurchaseItem.objects.filter(purchase_status='ready_for_issue').select_related('order', 'assembly_ref')
    q = request.GET.get('q', '').strip()
    if q:
        items = items.filter(Q(assembly_name__icontains=q) | Q(item_name__icontains=q))
    
    from django.db.models import Sum
    items = items.annotate(issued_qty=Sum('transactions__quantity', filter=Q(transactions__transaction_type='out')))
    
    # Вычисляем остаток для каждой позиции
    for item in items:
        item.remaining = (item.quantity_purchased or 0) - (item.issued_qty or 0)

    employees = Employee.objects.all().order_by('last_name', 'first_name')
    # Журнал выдач (последние 50)
    transactions = PurchaseTransaction.objects.filter(transaction_type='out').select_related('purchase_item', 'created_by').order_by('-created_at')[:50]
    return render(request, 'scanner/purchase_issue.html', {
        'items': items,
        'q': q,
        'employees': employees,
        'transactions': transactions,
    })

@login_required
@require_POST
def purchase_bulk_issue(request):
    """Групповая выдача с накладной"""
    data = json.loads(request.body)
    items_data = data.get('items', [])
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
        qty = min(int(d.get('quantity', 0)), item.quantity_purchased)
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
