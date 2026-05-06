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
    from openpyxl.styles import PatternFill

    rc = get_object_or_404(RouteCard, pk=route_card_id)
    ops = rc.operations.select_related('operation_type', 'worker').order_by('order')
    instance = rc.instance
    template_path = '/opt/qr_simple/templates/route_template.xlsx'
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active

    # --- Параметры страницы: А4 вертикально ---
    ws.page_setup.orientation = 'portrait'
    ws.page_setup.paperSize = 9  # A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    # Автоматически подгонять под одну страницу по ширине

    # --- Определяем главную сборку и родительскую подсборку ---
    order_item = instance.order_item
    root_item = None
    parent_item = None
    if order_item:
        current = order_item
        while current.parent:
            current = current.parent
        root_item = current
        parent_item = order_item.parent

    # --- Заполняем основные поля (используем адреса из нового шаблона) ---
    if root_item:
        ws['B2'] = f"{root_item.item.item_number} – {root_item.item.name}"
    else:
        ws['B2'] = instance.item.name

    detail_str = f"{instance.item.item_number} – {instance.item.name}"
    if parent_item and parent_item != root_item:
        detail_str += f" (входит в {parent_item.item.item_number} – {parent_item.item.name})"
    ws['B3'] = detail_str

    ws['B4'] = instance.created_at.strftime('%d.%m.%Y') if instance.created_at else ''
    ws['B5'] = instance.planned_quantity()

    # Материал
    if instance.item.material:
        mat_str = instance.item.material.name
        if instance.item.material.profile:
            mat_str += f" ({instance.item.material.profile})"
    else:
        mat_str = 'не указан'
    ws['B6'] = mat_str

    # Размер заготовки
    ws['B7'] = instance.item.blank_size or 'не указан'
    # Кол-во заготовок
    ws['B8'] = instance.item.blanks_per_item if instance.item.blanks_per_item else ''

    # --- Удаляем лишний QR-код из шаблона (если он там был как изображение) ---
    # Мы не будем вставлять QR программно, чтобы не дублировать.
    # Если в шаблоне уже есть QR, он останется. Если нет – не страшно.
    # Удалим все изображения, чтобы избежать дублирования (осторожно!)
    # Но лучше оставить, если пользователь сам вставил QR. Поэтому не трогаем.

    # --- Стили для таблицы ---
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    header_font = Font(bold=True)

    # --- Заголовки таблицы (строка 10) ---
    for col in range(1, 8):   # A-G
        cell = ws.cell(row=10, column=col)
        if cell.value:
            cell.font = header_font
            cell.border = thin_border
            cell.alignment = Alignment(horizontal='center', vertical='center')

    # --- Данные операций начиная с 11 строки ---
    for i, op in enumerate(ops):
        row = 11 + i
        ws.cell(row=row, column=1, value=op.operation_type.name).border = thin_border
        ws.cell(row=row, column=2, value=float(op.planned_hours)).border = thin_border
        ws.cell(row=row, column=3, value='').border = thin_border  # Оборудование
        ws.cell(row=row, column=4, value=op.worker.get_full_name() if op.worker else '').border = thin_border
        ws.cell(row=row, column=5, value=op.get_status_display()).border = thin_border
        ws.cell(row=row, column=6, value=f"{op.good_qty}/{op.bad_qty}").border = thin_border
        ws.cell(row=row, column=7, value=op.notes or '').border = thin_border

    # --- Подпись (после таблицы) ---
    signature_row = 11 + len(ops) + 1
    who = ''
    if hasattr(request.user, 'employee') and request.user.employee:
        emp = request.user.employee
        who = f"{emp.last_name} {emp.first_name} {emp.middle_name or ''}".replace('  ', ' ').strip()
    if not who:
        who = request.user.get_full_name() or request.user.username
    # Сотрём старую подпись в A12, если она там была
    ws['A12'] = ''
    ws.merge_cells(f'A{signature_row}:G{signature_row}')
    ws[f'A{signature_row}'] = f'Документ сформировал: {who}'
    ws[f'A{signature_row}'].font = Font(italic=True)
    ws[f'A{signature_row}'].alignment = Alignment(horizontal='left')

    # --- Автоширина столбцов ---
    # Задаём минимальную ширину для всех столбцов A-G
    for col_letter in ['A', 'B', 'C', 'D', 'E', 'F', 'G']:
        max_width = 10
        for row in range(10, 11 + len(ops)):
            cell = ws[f'{col_letter}{row}']
            if cell.value:
                # Приблизительный расчёт: 1.2 * длина строки (но лучше через openpyxl.utils.get_column_letter и т.д.)
                width = len(str(cell.value)) * 1.2 + 2
                if width > max_width:
                    max_width = width
        ws.column_dimensions[col_letter].width = max(min(max_width, 40), 8)

    # --- Сохраняем файл ---
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
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
                mat_name = material_name.strip()
                # Генерируем уникальный код, если материал новый
                base_code = ''.join(w[0].upper() for w in mat_name.split())[:6]
                code = base_code
                counter = 1
                while Material.objects.filter(code=code).exists():
                    code = f"{base_code}_{counter}"
                    counter += 1
                mat, _ = Material.objects.get_or_create(
                    name=mat_name,
                    defaults={
                        'code': code,
                        'profile': profile.strip() if profile else ''
                    }
                )
                item.material = mat
                item.save()
            # Сохраняем размер заготовки и количество, если есть в спецификации (столбцы 8 и 9, т.е. после profile)
            # Размер заготовки (столбец H, индекс 7) и кол-во заготовок (столбец I, индекс 8)
            raw_blank = row[7] if len(row) > 7 else ''
            raw_qty = row[8] if len(row) > 8 else 0
            # Преобразуем к строкам/числам аккуратно
            if raw_blank is not None and str(raw_blank).strip():
                item.blank_size = str(raw_blank).strip()
            if raw_qty is not None:
                try:
                    item.blanks_per_item = int(raw_qty)
                except (ValueError, TypeError):
                    pass
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


@login_required
def statistics_operations(request, type_name):
    from django.db.models import Sum
    from datetime import datetime, timedelta

    start_date = request.GET.get('start')
    end_date = request.GET.get('end')

    ops = RouteOperation.objects.select_related('operation_type', 'worker', 'route_card__instance__item', 'route_card__instance__order').filter(
        operation_type__name=type_name,
        status='completed'
    )

    if start_date and start_date != 'None':
        ops = ops.filter(completed_at__gte=start_date)
    if end_date and end_date != 'None':
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        ops = ops.filter(completed_at__lt=end_dt)

    total_count = ops.count()
    total_good = ops.aggregate(s=Sum('good_qty'))['s'] or 0
    total_bad = ops.aggregate(s=Sum('bad_qty'))['s'] or 0

    context = {
        'type_name': type_name,
        'start_date': start_date,
        'end_date': end_date,
        'total_count': total_count,
        'total_good': total_good,
        'total_bad': total_bad,
        'operations': ops.order_by('-completed_at')[:200],  # последние 200 записей
    }
    return render(request, 'scanner/statistics_operations.html', context)


@login_required
def statistics_operations_export(request, type_name):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Border, Side, PatternFill
    from datetime import datetime, timedelta
    from django.db.models import Sum

    start_date = request.GET.get('start')
    end_date = request.GET.get('end')

    ops = RouteOperation.objects.select_related(
        'operation_type', 'worker', 'route_card__instance__item', 'route_card__instance__order'
    ).filter(
        operation_type__name=type_name,
        status='completed'
    )

    if start_date and start_date != 'None':
        ops = ops.filter(completed_at__gte=start_date)
    if end_date and end_date != 'None':
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        ops = ops.filter(completed_at__lt=end_dt)

    wb = Workbook()
    ws = wb.active
    ws.title = f'{type_name}'

    # Заголовки
    headers = ['Дата завершения', 'Исполнитель', 'Деталь', 'Заказ', 'Годных', 'Брак', 'Примечание']
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='00557A', end_color='00557A', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border

    for row_num, op in enumerate(ops.order_by('-completed_at'), 2):
        ws.cell(row=row_num, column=1, value=op.completed_at.strftime('%d.%m.%Y %H:%M') if op.completed_at else '—').border = thin_border
        ws.cell(row=row_num, column=2, value=op.worker.get_full_name() if op.worker else '—').border = thin_border
        ws.cell(row=row_num, column=3, value=f"{op.route_card.instance.item.item_number} – {op.route_card.instance.item.name}").border = thin_border
        ws.cell(row=row_num, column=4, value=op.route_card.instance.order.order_number if op.route_card.instance.order else '—').border = thin_border
        ws.cell(row=row_num, column=5, value=op.good_qty).border = thin_border
        ws.cell(row=row_num, column=6, value=op.bad_qty).border = thin_border
        ws.cell(row=row_num, column=7, value=op.notes or '—').border = thin_border

    # Итоговая строка
    total_row = ops.count() + 2
    total_good = ops.aggregate(s=Sum('good_qty'))['s'] or 0
    total_bad = ops.aggregate(s=Sum('bad_qty'))['s'] or 0
    ws.cell(row=total_row, column=1, value='ИТОГО').font = Font(bold=True)
    ws.cell(row=total_row, column=5, value=total_good).font = Font(bold=True)
    ws.cell(row=total_row, column=6, value=total_bad).font = Font(bold=True)

    # Автоширина (пропускаем объединённые ячейки)
    import openpyxl
    for col_cells in ws.columns:
        max_length = 0
        column_letter = None
        # Получаем букву столбца из первой не-объединённой ячейки
        for cell in col_cells:
            if not isinstance(cell, openpyxl.cell.cell.MergedCell):
                try:
                    column_letter = cell.column_letter
                    break
                except AttributeError:
                    continue
        if not column_letter:
            continue
        for cell in col_cells:
            if not isinstance(cell, openpyxl.cell.cell.MergedCell):
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
        if max_length > 0:
            adjusted_width = (max_length + 2) * 1.2
            ws.column_dimensions[column_letter].width = adjusted_width

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    filename = f'{type_name}_{start_date or "все"}_{end_date or "все"}.xlsx'
    response['Content-Disposition'] = f'attachment; filename="{quote(filename)}"'
    wb.save(response)
    return response


@login_required
def statistics_export(request):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Border, Side, PatternFill, Alignment
    from datetime import datetime, timedelta
    from django.db.models import Sum

    start_date = request.GET.get('start')
    end_date = request.GET.get('end')

    # Фильтруем операции и склад
    ops = RouteOperation.objects.select_related('operation_type', 'worker')
    wh = WarehouseRecord.objects.all()

    if start_date and start_date != 'None':
        ops = ops.filter(completed_at__gte=start_date)
        wh = wh.filter(date__gte=start_date)
    if end_date and end_date != 'None':
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        ops = ops.filter(completed_at__lt=end_dt)
        wh = wh.filter(date__lt=end_dt)

    total_ops = ops.filter(status='completed').count()
    total_good = ops.filter(status='completed').aggregate(s=Sum('good_qty'))['s'] or 0
    total_bad = ops.filter(status='completed').aggregate(s=Sum('bad_qty'))['s'] or 0
    in_main = wh.filter(movement_type='in_main').aggregate(s=Sum('quantity'))['s'] or 0
    in_inter = wh.filter(movement_type='in_intermediate').aggregate(s=Sum('quantity'))['s'] or 0
    out_main = wh.filter(movement_type='out_main').aggregate(s=Sum('quantity'))['s'] or 0

    # По типам операций
    op_types = OperationType.objects.all()
    op_stats = []
    for ot in op_types:
        qs = ops.filter(operation_type=ot, status='completed')
        cnt = qs.count()
        good = qs.aggregate(s=Sum('good_qty'))['s'] or 0
        bad = qs.aggregate(s=Sum('bad_qty'))['s'] or 0
        if cnt > 0:
            op_stats.append({'name': ot.name, 'count': cnt, 'good': good, 'bad': bad})

    wb = Workbook()
    ws = wb.active
    ws.title = 'Сводный отчёт'

    # Стили
    title_font = Font(bold=True, size=14)
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='00557A', end_color='00557A', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    # Заголовок
    ws.merge_cells('A1:D1')
    ws['A1'] = 'Сводный отчёт за период: ' + (f'{start_date} – {end_date}' if start_date and start_date != 'None' else 'всё время')
    ws['A1'].font = title_font

    # Общие показатели
    ws['A3'] = 'Общие показатели'
    ws['A3'].font = Font(bold=True)
    indicators = [
        ('Выполнено операций', total_ops),
        ('Годных деталей', total_good),
        ('Брак', total_bad),
        ('Принято на основной склад', in_main),
        ('Принято на меж.операционный', in_inter),
        ('Выдано со склада', out_main),
    ]
    for i, (label, value) in enumerate(indicators, 4):
        ws.cell(row=i, column=1, value=label).font = Font(bold=True)
        ws.cell(row=i, column=2, value=value)
        ws.cell(row=i, column=1).border = thin_border
        ws.cell(row=i, column=2).border = thin_border

    # Таблица по типам операций
    table_start = len(indicators) + 5
    ws.cell(row=table_start, column=1, value='По типам операций').font = Font(bold=True)
    headers = ['Тип операции', 'Количество', 'Годных', 'Брак']
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=table_start+1, column=col_num, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal='center')

    for i, stat in enumerate(op_stats, table_start+2):
        ws.cell(row=i, column=1, value=stat['name']).border = thin_border
        ws.cell(row=i, column=2, value=stat['count']).border = thin_border
        ws.cell(row=i, column=3, value=stat['good']).border = thin_border
        ws.cell(row=i, column=4, value=stat['bad']).border = thin_border

    # Автоширина (пропускаем объединённые ячейки)
    import openpyxl
    for col_cells in ws.columns:
        max_length = 0
        column_letter = None
        # Получаем букву столбца из первой не-объединённой ячейки
        for cell in col_cells:
            if not isinstance(cell, openpyxl.cell.cell.MergedCell):
                try:
                    column_letter = cell.column_letter
                    break
                except AttributeError:
                    continue
        if not column_letter:
            continue
        for cell in col_cells:
            if not isinstance(cell, openpyxl.cell.cell.MergedCell):
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
        if max_length > 0:
            adjusted_width = (max_length + 2) * 1.2
            ws.column_dimensions[column_letter].width = adjusted_width

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    start_f = start_date if start_date and start_date != 'None' else 'все'
    end_f = end_date if end_date and end_date != 'None' else 'все'
    filename = f'Сводный_отчёт_{start_f}_{end_f}.xlsx'
    response['Content-Disposition'] = f'attachment; filename="{quote(filename)}"'
    wb.save(response)
    return response


@login_required
def statistics_compare(request):
    from django.db.models import Sum
    from datetime import datetime, timedelta

    start_a = request.GET.get('start_a')
    end_a = request.GET.get('end_a')
    start_b = request.GET.get('start_b')
    end_b = request.GET.get('end_b')

    def get_stats(start, end):
        ops = RouteOperation.objects.filter(status='completed')
        wh = WarehouseRecord.objects.all()
        if start and start != 'None':
            ops = ops.filter(completed_at__gte=start)
            wh = wh.filter(date__gte=start)
        if end and end != 'None':
            end_dt = datetime.strptime(end, '%Y-%m-%d') + timedelta(days=1)
            ops = ops.filter(completed_at__lt=end_dt)
            wh = wh.filter(date__lt=end_dt)
        return {
            'total_ops': ops.count(),
            'total_good': ops.aggregate(s=Sum('good_qty'))['s'] or 0,
            'total_bad': ops.aggregate(s=Sum('bad_qty'))['s'] or 0,
            'in_main': wh.filter(movement_type='in_main').aggregate(s=Sum('quantity'))['s'] or 0,
            'out_main': wh.filter(movement_type='out_main').aggregate(s=Sum('quantity'))['s'] or 0,
        }

    stats_a = get_stats(start_a, end_a)
    stats_b = get_stats(start_b, end_b)

    # Собираем таблицу сравнения
    rows = []
    labels = [
        ('total_ops', 'Выполнено операций'),
        ('total_good', 'Годных деталей'),
        ('total_bad', 'Брак'),
        ('in_main', 'Принято на склад'),
        ('out_main', 'Выдано со склада'),
    ]
    for key, label in labels:
        val_a = stats_a[key]
        val_b = stats_b[key]
        diff = val_b - val_a
        if val_a != 0:
            percent = round(diff / val_a * 100, 1)
        else:
            percent = 0 if val_b == 0 else 100  # если с нуля, считаем +100%
        rows.append({
            'label': label,
            'val_a': val_a,
            'val_b': val_b,
            'diff': diff,
            'percent': percent,
        })

    context = {
        'start_a': start_a,
        'end_a': end_a,
        'start_b': start_b,
        'end_b': end_b,
        'rows': rows,
    }
    return render(request, 'scanner/statistics_compare.html', context)
