from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.contrib import messages
from .models import *
from django.urls import reverse
from django.http import HttpResponse
from django.template.loader import render_to_string
import openpyxl
from io import BytesIO
import qrcode
from openpyxl.styles import Font, Border, Side, Alignment
from openpyxl.drawing.image import Image as XLImage
from urllib.parse import quote

@login_required
def dashboard(request):
    orders = Order.objects.all()
    return render(request, 'scanner/dashboard.html', {'orders': orders})

@login_required
def order_list(request):
    return dashboard(request)

@login_required
def order_detail(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    return render(request, 'scanner/order_detail.html', {'order': order})

@login_required
def order_tree(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    root_items = order.items.filter(parent__isnull=True)
    return render(request, 'scanner/order_tree.html', {
        'order': order,
        'root_items': root_items,
    })

@login_required
def instance_detail(request, item_number, serial):
    instance = get_object_or_404(ItemInstance, item__item_number=item_number, serial=serial)
    route_card = get_object_or_404(RouteCard, instance=instance)

    if request.method == 'POST':
        op_id = request.POST.get('operation_id')
        action = request.POST.get('action')
        op = get_object_or_404(RouteOperation, pk=op_id, route_card=route_card)

        if action == 'start' and op.status == 'pending':
            if instance.item.item_type == 'Сборочная единица' and not instance.all_components_ready():
                messages.error(request, 'Невозможно начать сборку: не все компоненты готовы.')
            else:
                op.status = 'in_progress'
                op.started_at = timezone.now()
                op.worker = request.user
                op.save()
        elif action == 'complete' and op.status == 'in_progress':
            op.status = 'completed'
            op.completed_at = timezone.now()
            op.good_qty = int(request.POST.get('good_qty', 0) or 0)
            op.bad_qty = int(request.POST.get('bad_qty', 0) or 0)
            op.notes = request.POST.get('notes', '')
            op.save()
            # Авто-приход на склад
            if op.operation_type.name == 'Прием на склад':
                movement = 'in_main'
            elif op.operation_type.name == 'Прием на меж.операционный склад':
                movement = 'in_intermediate'
            else:
                movement = None
            if movement:
                WarehouseRecord.objects.create(
                    instance=instance,
                    movement_type=movement,
                    quantity=op.good_qty,
                    employee=request.user.employee if hasattr(request.user, 'employee') else None,
                    basis=f'Завершение операции «{op.operation_type.name}»'
                )
            next_op = route_card.operations.filter(order=op.order + 1).first()
            if next_op and next_op.status == 'pending':
                pass
        return redirect('instance_detail', item_number=item_number, serial=serial)

    status_info = route_card.get_status()
    operations = route_card.operations.select_related('operation_type').order_by('order')
    assembly_status = instance.assembly_status() if instance.item.item_type == 'Сборочная единица' else None
    context = {
        'instance': instance,
        'route_card': route_card,
        'status_info': status_info,
        'operations': operations,
        'assembly_status': assembly_status,
    }
    return render(request, 'scanner/instance_detail.html', context)

@login_required
def supplement_instance(request, instance_id):
    old = get_object_or_404(ItemInstance, pk=instance_id)
    if old.shortage() > 0:
        new_serial = f"{old.serial}-дозапуск-{timezone.now().strftime('%Y%m%d%H%M')}"
        new_inst = ItemInstance.objects.create(
            item=old.item,
            serial=new_serial,
            quantity=old.shortage(),
            order=old.order,
            order_item=old.order_item
        )
        RouteCard.objects.create(instance=new_inst)
    return redirect('instance_detail', item_number=old.item.item_number, serial=old.serial)

@login_required
def route_card_print(request, route_card_id):
    rc = get_object_or_404(RouteCard, pk=route_card_id)
    ops = rc.operations.select_related('operation_type', 'worker').order_by('order')
    instance = rc.instance
    template_path = '/opt/qr_simple/templates/route_template.xlsx'
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active

    ws.page_setup.orientation = 'portrait'
    ws.page_setup.paperSize = 9
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0

    order_item = instance.order_item
    root_item = None
    parent_item = None
    if order_item:
        current = order_item
        while current.parent:
            current = current.parent
        root_item = current
        parent_item = order_item.parent

    if root_item:
        ws['B2'] = f"{root_item.item.item_number} – {root_item.item.name}"
    else:
        ws['B2'] = instance.item.name

    detail_str = f"{instance.item.item_number} – {instance.item.name}"
    if parent_item and parent_item != root_item:
        detail_str += f" (входит в {parent_item.item.item_number} – {parent_item.item.name})"
    ws['B3'] = detail_str

    ws['A4'] = 'Дата запуска:'
    ws['B4'] = instance.created_at.strftime('%d.%m.%Y') if instance.created_at else ''
    ws['A5'] = 'Количество деталей:'
    ws['B5'] = instance.planned_quantity()

    try:
        qr_url = request.build_absolute_uri(reverse('instance_detail', kwargs={
            'item_number': instance.item.item_number, 'serial': instance.serial}))
        img = qrcode.make(qr_url)
        buf = BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        xl_img = XLImage(buf)
        xl_img.width = 100
        xl_img.height = 100
        ws.add_image(xl_img, 'H1')
    except Exception:
        pass

    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'),
                         top=Side(style='thin'), bottom=Side(style='thin'))
    header_font = Font(bold=True)

    headers = ['Наименование операции', 'Норма времени, ч', 'Оборудование', 'Исполнитель', 'Статус', 'Годных/Брак', 'Примечание']
    for col_idx, title in enumerate(headers, start=2):
        cell = ws.cell(row=7, column=col_idx, value=title)
        cell.font = header_font
        cell.border = thin_border
        cell.alignment = Alignment(horizontal='center', vertical='center')

    for i, op in enumerate(ops):
        row = 8 + i
        ws.cell(row=row, column=1, value='').border = Border()
        ws.cell(row=row, column=2, value=op.operation_type.name).border = thin_border
        ws.cell(row=row, column=3, value=float(op.planned_hours)).border = thin_border
        ws.cell(row=row, column=4, value='').border = thin_border
        ws.cell(row=row, column=5, value=op.worker.get_full_name() if op.worker else '').border = thin_border
        ws.cell(row=row, column=6, value=op.get_status_display()).border = thin_border
        ws.cell(row=row, column=7, value=f"{op.good_qty}/{op.bad_qty}").border = thin_border
        ws.cell(row=row, column=8, value=op.notes or '').border = thin_border

    ws.column_dimensions['A'].width = 18
    for col in ['B', 'C', 'D', 'E', 'F', 'G', 'H']:
        max_width = 10
        for row in range(7, 8 + len(ops)):
            cell = ws[f'{col}{row}']
            if cell.value:
                width = len(str(cell.value)) * 1.2 + 2
                if width > max_width:
                    max_width = width
        ws.column_dimensions[col].width = min(max_width, 40)

    signature_row = 8 + len(ops) + 2
    who = ''
    if hasattr(request.user, 'employee'):
        emp = request.user.employee
        who = f"{emp.position}, {emp.last_name} {emp.first_name} {emp.middle_name or ''}".replace('  ', ' ').strip()
    else:
        who = request.user.get_full_name() or request.user.username

    ws.merge_cells(f'A{signature_row}:H{signature_row}')
    cell = ws[f'A{signature_row}']
    cell.value = f'Документ сформировал: {who}'
    cell.font = Font(italic=True)
    cell.alignment = Alignment(horizontal='left')

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    response = HttpResponse(output.read(),
                            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    safe_name = f"{instance.item.item_number}_{instance.item.name}_{instance.serial}.xlsx"
    safe_name = quote(safe_name.replace(' ', '_').replace('/', '_'))
    response['Content-Disposition'] = f'attachment; filename="{safe_name}"'
    return response

@login_required
def route_card_export(request, route_card_id):
    rc = get_object_or_404(RouteCard, pk=route_card_id)
    ops = rc.operations.select_related('operation_type').order_by('order')
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Маршрутная карта'
    try:
        qr_url = request.build_absolute_uri(reverse('instance_detail', kwargs={
            'item_number': rc.instance.item.item_number, 'serial': rc.instance.serial}))
        img = qrcode.make(qr_url)
        buf = BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        xl_img = XLImage(buf)
        xl_img.width = 80
        xl_img.height = 80
        ws.add_image(xl_img, 'A1')
        start_row = 6
    except Exception:
        start_row = 1

    headers = ['№', 'Операция', 'Норма', 'Статус', 'Годных', 'Брак', 'Исполнитель']
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=col, value=h)
        cell.font = Font(bold=True)
    for i, op in enumerate(ops, start=1):
        row = start_row + i
        ws.cell(row=row, column=1, value=op.order)
        ws.cell(row=row, column=2, value=op.operation_type.name)
        ws.cell(row=row, column=3, value=float(op.planned_hours))
        ws.cell(row=row, column=4, value=op.get_status_display())
        ws.cell(row=row, column=5, value=op.good_qty)
        ws.cell(row=row, column=6, value=op.bad_qty)
        ws.cell(row=row, column=7, value=op.worker.get_full_name() if op.worker else '')
    code = rc.instance.item.item_number.replace('/', '_')
    name_part = rc.instance.item.name.replace(' ', '_')
    filename = f"Маршрутка_{code}_{name_part}.xlsx"
    safe = quote(filename)
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f"attachment; filename*=UTF-8''{safe}"
    wb.save(response)
    return response

@login_required
def route_card_create(request, instance_id):
    instance = get_object_or_404(ItemInstance, pk=instance_id)
    if not hasattr(instance, 'route_card'):
        RouteCard.objects.create(instance=instance)
    return redirect('instance_detail', item_number=instance.item.item_number, serial=instance.serial)

@login_required
def order_import(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    if request.method == 'POST' and request.FILES.get('file'):
        import openpyxl
        file = request.FILES['file']
        wb = openpyxl.load_workbook(file)
        ws = wb.active
        order.items.all().delete()
        for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            designation, name, item_type, qty, parent_desig, material_name, profile = row[:7] if len(row) >= 7 else (row[0], row[1], row[2], row[3], row[4], None, None)
            if item_type:
                item_type = item_type.strip().capitalize()  # приводим к виду "Сборочная единица", "Деталь" и т.п.
            if not designation:
                continue
            item, _ = Item.objects.get_or_create(
                item_number=designation.strip(),
                defaults={'name': name.strip() if name else designation, 'item_type': item_type.strip() if item_type else 'Деталь'}
            )
            if material_name and material_name.strip():
                mat, _ = Material.objects.get_or_create(name=material_name.strip(), defaults={'profile': profile.strip() if profile else ''})
                item.material = mat
                item.save()
            parent = None
            if parent_desig and parent_desig.strip():
                parent_candidates = OrderItem.objects.filter(order=order, item__item_number=parent_desig.strip()).order_by('-id')
                if parent_candidates.exists():
                    parent = parent_candidates.first()
            oi = OrderItem.objects.create(order=order, item=item, quantity=int(qty) if qty else 1, parent=parent)
            if item_type in ('Сборочная единица', 'Деталь'):
                serial = f"{order.order_number}-{item.item_number}-{idx}"
                while ItemInstance.objects.filter(serial=serial).exists():
                    serial += f"-{timezone.now().strftime('%H%M%S')}"
                inst = ItemInstance.objects.create(item=item, serial=serial, quantity=int(qty) if qty else 1, order=order, order_item=oi)
                RouteCard.objects.create(instance=inst)
                serial = f"{order.order_number}-{item.item_number}-{idx}"
                while ItemInstance.objects.filter(serial=serial).exists():
                    serial += f"-{timezone.now().strftime('%H%M%S')}"
                inst = ItemInstance.objects.create(item=item, serial=serial, quantity=int(qty) if qty else 1, order=order, order_item=oi)
                RouteCard.objects.create(instance=inst)
                serial = f"{order.order_number}-{item.item_number}-{idx}"
                while ItemInstance.objects.filter(serial=serial).exists():
                    serial += f"-{timezone.now().strftime('%H%M%S')}"
                inst = ItemInstance.objects.create(item=item, serial=serial, quantity=int(qty) if qty else 1, order=order, order_item=oi)
                RouteCard.objects.create(instance=inst)
    return redirect('order_tree', order_id=order.id)

@login_required
def warehouse_dashboard(request):
    instances = ItemInstance.objects.select_related('item').all()
    main_data = []
    intermediate_data = []
    employee_list = Employee.objects.filter(is_active=True)
    for inst in instances:
        # Основной склад (in_main - out_main)
        total_in_main = inst.warehouse_records.filter(movement_type='in_main').aggregate(
            s=models.Sum('quantity'))['s'] or 0
        total_out_main = inst.warehouse_records.filter(movement_type='out_main').aggregate(
            s=models.Sum('quantity'))['s'] or 0
        balance_main = total_in_main - total_out_main
        # Меж.операционный склад (in_intermediate - out_intermediate)
        total_in_inter = inst.warehouse_records.filter(movement_type='in_intermediate').aggregate(
            s=models.Sum('quantity'))['s'] or 0
        total_out_inter = inst.warehouse_records.filter(movement_type='out_intermediate').aggregate(
            s=models.Sum('quantity'))['s'] or 0
        balance_inter = total_in_inter - total_out_inter
        # Определяем головную сборку
        root_name = ''
        if inst.order_item:
            current = inst.order_item
            while current.parent:
                current = current.parent
            root_name = f"{current.item.item_number} – {current.item.name}"
        if balance_main > 0:
            main_data.append({'instance': inst, 'balance': balance_main, 'assembly': root_name})
        if balance_inter > 0:
            intermediate_data.append({'instance': inst, 'balance': balance_inter, 'assembly': root_name})
    records = WarehouseRecord.objects.select_related('instance__item', 'employee').order_by('-date')[:200]
    context = {
        'main_data': main_data,
        'intermediate_data': intermediate_data,
        'records': records,
        'employee_list': employee_list,
    }
    return render(request, 'scanner/warehouse_dashboard.html', context)

def warehouse_issue(request):
    if request.method == 'POST':
        inst_id = request.POST.get('instance_id')
        qty = int(request.POST.get('quantity', 0))
        recipient_val = request.POST.get('recipient', '')
        basis = request.POST.get('basis', '')
        notes = request.POST.get('notes', '')
        inst = get_object_or_404(ItemInstance, pk=inst_id)
        total_in = inst.warehouse_records.filter(
            movement_type__in=['in_main', 'in']
        ).aggregate(s=models.Sum('quantity'))['s'] or 0
        total_out = inst.warehouse_records.filter(
            movement_type__in=['out_main', 'out']
        ).aggregate(s=models.Sum('quantity'))['s'] or 0
        balance = total_in - total_out
        if qty <= 0 or qty > balance:
            messages.error(request, f'Можно выдать не более {balance} шт. Вы запросили {qty}.')
        else:
            recipient_str = ''
            if recipient_val:
                try:
                    emp_id = int(recipient_val)
                    emp = Employee.objects.get(pk=emp_id)
                    recipient_str = f"{emp.last_name} {emp.first_name} {emp.middle_name or ''}".strip()
                except (ValueError, Employee.DoesNotExist):
                    recipient_str = str(recipient_val).strip()
            WarehouseRecord.objects.create(
                instance=inst, movement_type='out_main', quantity=qty,
                employee=request.user.employee if hasattr(request.user, 'employee') else None,
                recipient=recipient_str, basis=basis,
                notes=notes
            )
            messages.success(request, f'Выдано {qty} шт. со склада.')
        return redirect('warehouse')
    return redirect('warehouse')


# --- Статистика ---
@login_required
def statistics(request):
    from django.db.models import Sum, Count, Q
    from datetime import datetime, timedelta

    # Параметры фильтрации
    start_date = request.GET.get('start')
    end_date = request.GET.get('end')

    # Базовые запросы
    ops = RouteOperation.objects.select_related('operation_type', 'worker')
    wh = WarehouseRecord.objects.select_related('instance__item', 'employee')

    if start_date:
        ops = ops.filter(completed_at__gte=start_date)
        wh = wh.filter(date__gte=start_date)
    if end_date:
        # end_date включительно до конца дня
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        ops = ops.filter(completed_at__lt=end_dt)
        wh = wh.filter(date__lt=end_dt)

    # Суммарные показатели
    total_ops = ops.filter(status='completed').count()
    total_good = ops.filter(status='completed').aggregate(s=Sum('good_qty'))['s'] or 0
    total_bad = ops.filter(status='completed').aggregate(s=Sum('bad_qty'))['s'] or 0

    # По типам операций
    op_types = OperationType.objects.all()
    op_stats = []
    for ot in op_types:
        qs = ops.filter(operation_type=ot, status='completed')
        cnt = qs.count()
        good = qs.aggregate(s=Sum('good_qty'))['s'] or 0
        bad = qs.aggregate(s=Sum('bad_qty'))['s'] or 0
        if cnt > 0:
            op_stats.append({
                'name': ot.name,
                'count': cnt,
                'good': good,
                'bad': bad,
            })

    # Складские движения
    in_main = wh.filter(movement_type='in_main').aggregate(s=Sum('quantity'))['s'] or 0
    in_inter = wh.filter(movement_type='in_intermediate').aggregate(s=Sum('quantity'))['s'] or 0
    out_main = wh.filter(movement_type='out_main').aggregate(s=Sum('quantity'))['s'] or 0

    # Для графика выпуска по дням (последние 30 дней)
    from_date = datetime.now() - timedelta(days=30)
    daily_ops = RouteOperation.objects.filter(
        status='completed', completed_at__gte=from_date
    ).extra(select={'day': 'date(completed_at)'}).values('day').annotate(
        good=Sum('good_qty'), bad=Sum('bad_qty'), count=Count('id')
    ).order_by('day')

    context = {
        'start_date': start_date,
        'end_date': end_date,
        'total_ops': total_ops,
        'total_good': total_good,
        'total_bad': total_bad,
        'op_stats': op_stats,
        'in_main': in_main,
        'in_inter': in_inter,
        'out_main': out_main,
        'daily_ops': list(daily_ops),
        'op_types_json': [{'name': s['name'], 'count': s['count']} for s in op_stats],
    }
    return render(request, 'scanner/statistics.html', context)


def logout_view(request):
    logout(request)
    return redirect('/accounts/login/')
