from scanner.models import RouteCard
from datetime import datetime, timedelta, date
from django.db.models import Q, Q,  Count, Max, Q, F, Count, Max, Q, F, Count, Max, Q, F, Count, Max, Q, F, Q
from django.db.models import Sum
from django.conf import settings
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
    orders = Order.objects.all().order_by('-created_at')
    return render(request, 'scanner/dashboard.html', {'orders': orders})

@login_required
def order_list(request):
    from collections import defaultdict
    
    orders = Order.objects.all().order_by('-created_at')
    contract = request.GET.get('contract', '')
    if contract:
        orders = orders.filter(
            Q(order_number=contract) |
            Q(items__item__item_number__icontains=contract) |
            Q(items__item__name__icontains=contract)
        ).distinct()
    
    group_mode = request.GET.get('group') == '1'
    
    if group_mode:
        groups = defaultdict(list)
        for order in orders:
            project = order.project.strip() if order.project else 'Без проекта'
            groups[project].append(order)
        sorted_groups = sorted(groups.items(), key=lambda x: (x[0] == 'Без проекта', -len(x[1])))
    else:
        sorted_groups = None
    
    return render(request, 'scanner/order_list.html', {
        'orders': orders,
        'groups': sorted_groups,
        'group_mode': group_mode,
        'contracts': Order.objects.values_list('order_number', flat=True).distinct(),
        'selected_contract': contract,
    })

@login_required

@login_required






def order_detail(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    return render(request, 'scanner/order_detail.html', {'order': order})

@login_required
def order_tree(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    
    from django.core.paginator import Paginator
    
    # Все позиции верхнего уровня
    all_root_items = order.items.filter(parent__isnull=True).order_by('item__item_number')
    
    # Пагинация: 100 позиций на странице
    paginator = Paginator(all_root_items, 100)
    page_number = request.GET.get('page', 1)
    root_items_page = paginator.get_page(page_number)
    
    # Загружаем связанные данные только для отображаемых позиций
    root_items = root_items_page.object_list.prefetch_related(
        'instances__route_card__operations__operation_type',
        'children__instances__route_card__operations__operation_type'
    ).select_related('item')
    
    # Приоритетные операции (для всего заказа, чтобы фильтр работал)
    priority_ids = set(
        RouteOperation.objects.filter(
            route_card__instance__order=order,
            operation_type__name__in=['Гальваника', 'Расточная', 'Кооперация']
        ).values_list('route_card__instance_id', flat=True).distinct()
    )
    return render(request, 'scanner/order_tree.html', {
        'order': order,
        'root_items': root_items,
        'priority_ids': priority_ids,
        'is_paginated': paginator.num_pages > 1,
        'page_obj': root_items_page,
    })


@login_required
def instance_detail(request, item_number, serial):
    instance = get_object_or_404(ItemInstance, item__item_number=item_number, serial=serial)
    route_card = get_object_or_404(RouteCard, instance=instance)

    if request.method == 'POST':
        op_id = request.POST.get('operation_id')
        action = request.POST.get('action')
        op = get_object_or_404(RouteOperation, pk=op_id, route_card=route_card)

        # Проверка прав по ролям при старте
        if hasattr(request.user, 'employee'):
            role = request.user.employee.role
            op_name = op.operation_type.name.lower()
            is_warehouse = 'прием на' in op_name
            is_control = 'контрол' in op_name

            if role in ('admin', 'master', 'dispatcher'):
                pass  # admin, master, dispatcher могут всё
            elif role == 'storekeeper' and not is_warehouse:
                messages.error(request, 'Кладовщик выполняет только складские операции.')
                anchor = f'#operation-{op.id}'; return redirect(f'/instance/{item_number}/{serial}/' + anchor)
            elif role == 'controller' and not is_control:
                messages.error(request, 'Контролёр выполняет только контрольные операции.')
                anchor = f'#operation-{op.id}'; return redirect(f'/instance/{item_number}/{serial}/' + anchor)
            elif role == 'worker' and (is_warehouse or is_control):
                messages.error(request, 'Рабочий не выполняет складские и контрольные операции.')
                anchor = f'#operation-{op.id}'; return redirect(f'/instance/{item_number}/{serial}/' + anchor)
            elif role in ('supervisor', 'technologist'):
                msg = 'Руководитель' if role == 'supervisor' else 'Технолог'
                messages.error(request, f'{msg} не выполняет производственные операции.')
                anchor = f'#operation-{op.id}'; return redirect(f'/instance/{item_number}/{serial}/' + anchor)

        if action == 'start' and op.status == 'pending':
            if instance.item.item_type == 'Сборочная единица' and not instance.all_components_ready():
                messages.error(request, 'Невозможно начать сборку: не все компоненты готовы.')
            else:
                op.status = 'in_progress'
                op.started_at = timezone.now()
                op.worker = request.user
                op.save()

        elif action == 'complete' and op.status == 'in_progress':
            # Проверка прав по ролям при завершении
            if hasattr(request.user, 'employee'):
                role = request.user.employee.role
                op_name = op.operation_type.name.lower()
                is_warehouse = 'прием на' in op_name
                is_control = 'контрол' in op_name

                if role in ('admin', 'master', 'dispatcher'):
                    pass  # admin, master, dispatcher могут всё
                elif role == 'storekeeper' and not is_warehouse:
                    messages.error(request, 'Кладовщик выполняет только складские операции.')
                    anchor = f'#operation-{op.id}'; return redirect(f'/instance/{item_number}/{serial}/' + anchor)
                elif role == 'controller' and not is_control:
                    messages.error(request, 'Контролёр выполняет только контрольные операции.')
                    anchor = f'#operation-{op.id}'; return redirect(f'/instance/{item_number}/{serial}/' + anchor)
                elif role == 'worker' and (is_warehouse or is_control):
                    messages.error(request, 'Рабочий не выполняет складские и контрольные операции.')
                    anchor = f'#operation-{op.id}'; return redirect(f'/instance/{item_number}/{serial}/' + anchor)
                elif role in ('supervisor', 'technologist'):
                    msg = 'Руководитель' if role == 'supervisor' else 'Технолог'
                    messages.error(request, f'{msg} не выполняет производственные операции.')
                    anchor = f'#operation-{op.id}'; return redirect(f'/instance/{item_number}/{serial}/' + anchor)

            new_good = int(request.POST.get('good_qty', 0) or 0)
            new_bad = int(request.POST.get('bad_qty', 0) or 0)

            planned = instance.total_planned_quantity()
            current_good = op.good_qty or 0
            current_bad = op.bad_qty or 0

            # Проверка: если не введено ни годных, ни брака, но план ещё не исчерпан
            # (проверим позже, после подсчёта previous bad)

            current_good = op.good_qty or 0
            current_bad = op.bad_qty or 0

            # Рассчитываем оставшийся план для этой операции
            prev_ops = route_card.operations.filter(order__lt=op.order)
            total_bad_before = prev_ops.aggregate(total=Sum('bad_qty'))['total'] or 0
            remaining_before = planned - total_bad_before

            # Проверки на превышение остатка
            if new_good > remaining_before:
                request.session['error_op_id'] = op.id
                request.session['error_message'] = f'Количество годных не может превышать остаток ({remaining_before} шт.).'
                anchor = f'#operation-{op.id}'; return redirect(f'/instance/{item_number}/{serial}/' + anchor)
            if new_bad > remaining_before:
                request.session['error_op_id'] = op.id
                request.session['error_message'] = f'Количество брака не может превышать остаток ({remaining_before} шт.).'
                anchor = f'#operation-{op.id}'; return redirect(f'/instance/{item_number}/{serial}/' + anchor)
            if (current_good + current_bad + new_good + new_bad) > remaining_before:
                request.session['error_op_id'] = op.id
                request.session['error_message'] = f'Сумма годных и брака не может превышать остаток ({remaining_before} шт.).'
                anchor = f'#operation-{op.id}'; return redirect(f'/instance/{item_number}/{serial}/' + anchor)

            # Проверяем, не исчерпан ли план браком на предыдущих операциях
            prev_ops = route_card.operations.filter(order__lt=op.order)
            total_bad_before = prev_ops.aggregate(total=Sum('bad_qty'))['total'] or 0
            # Оставшиеся детали = план - брак предыдущих операций (годные не вычитаем)
            remaining_before = planned - total_bad_before

            # Если план ещё не исчерпан, но не введено ни годных, ни брака — ошибка
            if remaining_before > 0 and new_good == 0 and new_bad == 0 and (current_good + current_bad) == 0:
                request.session['error_op_id'] = op.id
                anchor = f'#operation-{op.id}'
                return redirect(f'/instance/{item_number}/{serial}/' + anchor)

            # Если план уже исчерпан браком, разрешаем завершить с нулями
            if remaining_before <= 0:
                op.good_qty = current_good + 0
                op.bad_qty = current_bad + 0
                op.status = 'completed'
                op.completed_at = timezone.now()
                op.notes = request.POST.get('notes', '') if request.POST.get('notes') else f'План исчерпан браком до операции (брак: {total_bad_before})'
                # Логируем
                worker_name = f"{request.user.employee.last_name} {request.user.employee.first_name}" if hasattr(request.user, 'employee') else request.user.username
                timestamp = (timezone.now() + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')
                new_log_entry = f"{timestamp} — {worker_name} (автозавершение: план исчерпан браком)"
                op.worker_log = (op.worker_log + '\n' + new_log_entry) if op.worker_log else new_log_entry
                op.worker = request.user
                op.save()
                messages.warning(request, 'Операция завершена автоматически: план исчерпан браком на предыдущих операциях.')
                anchor = f'#operation-{op.id}'
                return redirect(f'/instance/{item_number}/{serial}/' + anchor)

            # накапливаем годные и брак
            op.good_qty = current_good + new_good
            op.bad_qty = current_bad + new_bad
            # Дописываем исполнителя в историю
            worker_name = f"{request.user.employee.last_name} {request.user.employee.first_name}" if hasattr(request.user, 'employee') else request.user.username
            timestamp = (timezone.now() + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')
            new_log_entry = f"{timestamp} — {worker_name} (+{new_good} годных, +{new_bad} брак)"
            op.worker_log = (op.worker_log + '\n' + new_log_entry) if op.worker_log else new_log_entry
            # Обновляем текущего исполнителя
            op.worker = request.user
            # Обновляем исполнителя на того, кто фактически выполнил работу
            op.worker = request.user
            notes = request.POST.get('notes', '')
            if notes:
                op.notes = notes

            # Проверяем, выполнена ли норма
            planned = instance.total_planned_quantity()
            is_warehouse = op.operation_type.name in ('Прием на меж.операционный склад', 'Прием на склад')
            is_control = op.operation_type.name in ('Контрольная', 'Контроль ОТК')
            
            # Для складских и контрольных операций: завершаем только при достижении плана
            if is_warehouse or is_control:
                if (op.good_qty + op.bad_qty) >= (planned - total_bad_before):
                    op.status = 'completed'
                    op.completed_at = timezone.now()
                # если план не достигнут — остаётся in_progress
            elif (op.good_qty + op.bad_qty) >= (planned - total_bad_before):
                # Для обычных операций — завершаем при достижении плана
                op.status = 'completed'
                op.completed_at = timezone.now()
            
            op.save()

            # Складской приход создаём при каждом частичном завершении
            if is_warehouse:
                movement = 'in_main' if op.operation_type.name == 'Прием на склад' else 'in_intermediate'
                # Важно: фиксируем только новое количество, а не общее
                new_quantity = new_good  # количество, которое ввели сейчас
                if new_quantity > 0:
                    WarehouseRecord.objects.create(
                        instance=instance,
                        movement_type=movement,
                        quantity=new_quantity,
                        employee=request.user.employee if hasattr(request.user, 'employee') else None,
                        basis=f'Завершение операции «{op.operation_type.name}» (частичное)',
                        notes=(op.notes if op.notes else '')
                    )
                # активируем следующую операцию
                next_op = route_card.operations.filter(order=op.order + 1).first()
                if next_op and next_op.status == 'pending':
                    pass
            else:
                # операция остаётся в работе
                op.save()

        anchor = f'#operation-{op.id}'; return redirect(f'/instance/{item_number}/{serial}/' + anchor)

    status_info = route_card.get_status()
    operations = route_card.operations.select_related('operation_type', 'worker').order_by('order')
    planned = instance.total_planned_quantity()
    for op in operations:
        op.remaining = planned - (op.good_qty or 0)
        op.requires_location = op.operation_type.name in ('Прием на склад', 'Прием на меж.операционный склад')
    # помечаем складские операции как требующие место
    for op in operations:
        op.requires_location = op.operation_type.name in ('Прием на склад', 'Прием на меж.операционный склад')

    assembly_status = instance.assembly_status() if instance.item.item_type == 'Сборочная единица' else None


    # Полный путь до главной сборки
    root_assembly_path = []
    if instance.order_item:
        current = instance.order_item
        while current:
            label = current.item.item_number + ' – ' + current.item.name if current.item else '—'
            root_assembly_path.insert(0, label)
            current = current.parent
    # Общее количество на главную сборку
    total_assembly_qty = 0
    related_instances = []
    if instance.order_item:
        root = instance.order_item
        while root.parent:
            root = root.parent
        # Суммируем количество одинаковых деталей (по item) во всей главной сборке
        from collections import defaultdict
        qty_by_item = defaultdict(int)
        def collect(item):
            children = item.children.all()
            if not children:
                qty_by_item[item.item_id] += item.quantity
            else:
                for child in children:
                    collect(child)
        collect(root)
        total_assembly_qty = qty_by_item.get(instance.item_id, 0)
        # Собираем все экземпляры этой же детали в главной сборке
        related_instances = []
        def collect_instances(item):
            children = item.children.all()
            if not children and item.item_id == instance.item_id:
                related_instances.extend(list(item.instances.all()))
            else:
                for child in children:
                    collect_instances(child)
        collect_instances(root)

    context = {
        'instance': instance,
        'total_assembly_qty': total_assembly_qty,
        'related_instances': related_instances,
        'route_card': route_card,
        'status_info': status_info,
        'operations': operations,
        'assembly_status': assembly_status,
        'root_assembly_path': root_assembly_path,
    }
    context['error_op_id'] = request.session.pop('error_op_id', None)
    context['error_message'] = request.session.pop('error_message', None)
    return render(request, 'scanner/instance_detail.html', context)

@login_required
def supplement_instance(request, instance_id):
    old = get_object_or_404(ItemInstance, pk=instance_id)
    if old.shortage() > 0:
        base_serial = f"{old.serial}-дозапуск-{timezone.now().strftime('%Y%m%d%H%M')}"
        new_serial = base_serial
        counter = 1
        # Гарантируем уникальность серийного номера
        while ItemInstance.objects.filter(serial=new_serial).exists():
            new_serial = f"{base_serial}-{counter}"
            counter += 1
        new_inst = ItemInstance.objects.create(
            item=old.item,
            serial=new_serial,
            quantity=old.shortage(),
            setup_quantity=old.setup_quantity,
            order=old.order,
            order_item=old.order_item
        )
        new_card = RouteCard.objects.create(instance=new_inst)
        # Копируем операции из исходной маршрутной карты
        if hasattr(old, 'route_card') and old.route_card:
            for op in old.route_card.operations.all():
                RouteOperation.objects.create(
                    route_card=new_card,
                    operation_type=op.operation_type,
                    order=op.order,
                    planned_hours=op.planned_hours,
                    status='pending',
                    good_qty=0,
                    bad_qty=0
                )

    return redirect('instance_detail', item_number=old.item.item_number, serial=old.serial)

@login_required
def route_card_print(request, route_card_id):
    from openpyxl.styles import PatternFill, Alignment as XlAlignment, Font as XlFont, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.cell.cell import MergedCell
    from datetime import datetime
    from urllib.parse import quote

    rc = get_object_or_404(RouteCard, pk=route_card_id)
    if not rc.instance:
        messages.error(request, 'Маршрутная карта повреждена.')
        return redirect('home')
    instance = rc.instance
    ops = rc.operations.select_related('operation_type', 'worker').order_by('order')

    template_path = '/opt/qr_simple/templates/route_template.xlsx'
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active

    # Удаляем ВСЕ старые изображения с ЛИСТА (не из книги)
    for img in ws._images[:]:
        ws._images.remove(img)

    # Сбрасываем объединения
    for merged_range in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(merged_range))

    # Параметры страницы
    ws.page_setup.orientation = 'portrait'
    ws.page_setup.paperSize = 9
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0

    # Сборки
    order_item = instance.order_item
    root_item = None
    parent_item = None
    if order_item:
        current = order_item
        while current.parent:
            current = current.parent
        root_item = current
        parent_item = order_item.parent

    # Стили
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'),
                         top=Side(style='thin'), bottom=Side(style='thin'))
    header_font = XlFont(bold=True, size=11)
    data_font = XlFont(size=11)
    center_wrap = XlAlignment(horizontal='center', vertical='center', wrap_text=True)
    left_wrap   = XlAlignment(horizontal='left', vertical='center', wrap_text=True)

    def safe_write(ws, row, col, value, alignment=None):
        cell = ws.cell(row=row, column=col)
        if not isinstance(cell, MergedCell):
            cell.value = value
            if alignment:
                cell.alignment = alignment
            return cell
        return None

    def apply_border(ws, row, col, border):
        cell = ws.cell(row=row, column=col)
        if not isinstance(cell, MergedCell):
            cell.border = border

    # ---------- Заголовок A1:D1 ----------
    ws.merge_cells('A1:D1')
    safe_write(ws, 1, 1, 'Маршрутно-операционный лист', center_wrap)
    ws['A1'].font = XlFont(bold=True, size=14)

    # ---------- Изделие (строка 2) ----------
    safe_write(ws, 2, 1, 'Изделие:', XlAlignment(horizontal='center', vertical='center'))
    ws.merge_cells('B2:D2')
    if root_item:
        safe_write(ws, 2, 2, f"{root_item.item.item_number} – {root_item.item.name} ({root_item.quantity} шт.)", center_wrap)
    else:
        safe_write(ws, 2, 2, instance.item.name, center_wrap)
    ws.row_dimensions[2].height = 40
    for c in range(1, 5):
        apply_border(ws, 2, c, thin_border)

    # ---------- Деталь (строка 3) ----------
    safe_write(ws, 3, 1, 'Деталь:', left_wrap)
    apply_border(ws, 3, 1, thin_border)
    ws.merge_cells('B3:D3')
    detail_str = f"{instance.item.item_number} – {instance.item.name}"
    if parent_item and parent_item != root_item:
        detail_str += f" (входит в {parent_item.item.item_number} – {parent_item.item.name})"
    safe_write(ws, 3, 2, detail_str, center_wrap)
    ws.row_dimensions[3].height = 35
    for c in range(2, 5):
        apply_border(ws, 3, c, thin_border)

    # ---------- Дата запуска (строка 4) ----------
    ws.merge_cells('B4:D4')
    safe_write(ws, 4, 2, instance.created_at.strftime('%d.%m.%Y') if instance.created_at else '', center_wrap)
    for c in range(1, 5):
        apply_border(ws, 4, c, thin_border)

    # ---------- Количество (строка 5) ----------
    ws.merge_cells('B5:D5')
    safe_write(ws, 5, 2, f"{instance.total_planned_quantity()} (план сборки: {instance.quantity}, настроечные: {instance.setup_quantity})", center_wrap)
    for c in range(1, 5):
        apply_border(ws, 5, c, thin_border)

    # ---------- Материал (строка 6) ----------
    ws.merge_cells('B6:D6')
    if instance.item.material:
        mat_str = instance.item.material.name
    else:
        mat_str = 'не указан'
    safe_write(ws, 6, 2, mat_str, center_wrap)
    for c in range(1, 5):
        apply_border(ws, 6, c, thin_border)

    # ---------- Профиль (если есть) ----------
    has_profile = bool(instance.item.profile)
    offset = 0
    if has_profile:
        ws.insert_rows(7)
        safe_write(ws, 7, 1, 'Сортамент:', left_wrap)
        apply_border(ws, 7, 1, thin_border)
        # Применяем границы к B7, C7, D7 до объединения
        for c in range(2, 5):
            apply_border(ws, 7, c, thin_border)
        ws.merge_cells('B7:D7')
        safe_write(ws, 7, 2, instance.item.profile, center_wrap)
        offset = 1

    # ---------- Размер заготовки ----------
    ws.merge_cells(f'B{7+offset}:D{7+offset}')
    safe_write(ws, 7+offset, 2, (instance.get_blank_size() if instance.get_blank_size() else 'не указан'), center_wrap)
    for c in range(1, 5):
        apply_border(ws, 7+offset, c, thin_border)

    # ---------- Кол-во заготовок ----------
    ws.merge_cells(f'B{8+offset}:D{8+offset}')
    blanks_qty = instance.get_blanks_per_item()
    if not blanks_qty and instance.item.item_type == 'Сборочная единица':
        blanks_qty = instance.total_planned_quantity()
    safe_write(ws, 8+offset, 2, blanks_qty if blanks_qty else 'не указан', center_wrap)
    for c in range(1, 5):
        apply_border(ws, 8+offset, c, thin_border)

    # Принудительно устанавливаем единый шрифт для всех заполненных ячеек
    for r in range(2, 9 + offset + 1):
        for c in range(1, 8):
            cell = ws.cell(row=r, column=c)
            if cell.value and not isinstance(cell, MergedCell):
                cell.font = data_font

    # ---------- Пустая строка-разделитель ----------
    sep_row = 9 + offset
    for c in range(1, 6):
        cell = ws.cell(row=sep_row, column=c)
        if not isinstance(cell, MergedCell):
            cell.value = ''
            cell.border = Border()

    # ---------- Заголовки таблицы (5 столбцов) ----------
    header_row = 10 + offset
    for col in range(1, 8):
        cell = ws.cell(row=header_row, column=col)
        if not isinstance(cell, MergedCell):
            cell.value = None
            cell.border = Border()
    headers = ['Наименование операции', 'Норма времени, ч', 'Исполнитель', 'Годных/Брак', 'Примечание']
    for col_idx, title in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=title)
        cell.font = header_font
        cell.border = thin_border
        cell.alignment = center_wrap

    # ---------- Данные операций ----------
    for i, op in enumerate(ops):
        row = 11 + offset + i
        safe_write(ws, row, 1, op.operation_type.name, left_wrap)
        safe_write(ws, row, 2, float(op.planned_hours), center_wrap)
        safe_write(ws, row, 3, op.worker.get_full_name() if op.worker else '', left_wrap)
        safe_write(ws, row, 4, f"{op.good_qty}/{op.bad_qty}", center_wrap)
        pass  # Примечание скрыто
        for c in range(1, 6):
            apply_border(ws, row, c, thin_border)

    # ---------- Ширина столбцов ----------
    col_widths = {1: 25, 2: 12, 3: 18, 4: 16, 5: 0}  # Примечание скрыто
    for col_idx, w in col_widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = w
    for col_letter in ['F', 'G']:
        ws.column_dimensions[col_letter].width = 0  # Скрыто
    for r in range(1, 11 + offset + len(ops)):
        for c in ['F', 'G']:
            cell = ws[f'{c}{r}']
            if not isinstance(cell, MergedCell):
                cell.border = Border()
    for r in range(header_row, 11 + offset + len(ops)):
        ws.row_dimensions[r].height = None

    # ---------- Подпись ----------
    signature_row = 11 + offset + len(ops) + 1
    who = ''
    if hasattr(request.user, 'employee') and request.user.employee:
        emp = request.user.employee
        who = f"{emp.last_name} {emp.first_name} {emp.middle_name or ''}".replace('  ', ' ').strip()
    if not who:
        who = request.user.get_full_name() or request.user.username
    ws.merge_cells(f'A{signature_row}:E{signature_row}')
    c = ws[f'A{signature_row}']
    if not isinstance(c, MergedCell):
        c.value = f'Документ сформировал: {who}'
        c.font = XlFont(italic=False, size=11)
        c.alignment = XlAlignment(horizontal='left', vertical='center')

    # ---------- Дата печати ----------
    print_date_row = signature_row + 1
    ws.merge_cells(f'A{print_date_row}:E{print_date_row}')
    c = ws[f'A{print_date_row}']
    if not isinstance(c, MergedCell):
        c.value = f'Дата печати: {datetime.now().strftime("%d.%m.%Y %H:%M")}'
        c.font = XlFont(italic=False, size=11)
        c.alignment = XlAlignment(horizontal='left', vertical='center')

    # ---------- QR-код (ПОД ДАТОЙ ПЕЧАТИ) ----------
    try:
        from django.urls import reverse
        raw_url = request.build_absolute_uri(
            reverse('instance_detail', kwargs={
                'item_number': instance.item.item_number,
                'serial': instance.serial
            })
        )
        qr_url = quote(raw_url, safe='/:?=&%')
        img = qrcode.make(qr_url)
        buf = BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        xl_img = XLImage(buf)
        xl_img.width = 110
        xl_img.height = 100
        qr_row = print_date_row + 2
        ws.add_image(xl_img, f'A{qr_row}')
    except Exception:
        pass

    # ---------- Сохранение ----------
    # Принудительно устанавливаем выравнивание для A2
    from openpyxl.styles import Alignment
    ws['A2'].alignment = Alignment(horizontal='left', vertical='center')
    # Принудительно устанавливаем Calibri 11pt для всех заполненных ячеек
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
        for cell in row:
            if cell.value is not None and not isinstance(cell, openpyxl.cell.cell.MergedCell):
                cell.font = openpyxl.styles.Font(name='Calibri', size=11)
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
def update_order_project(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, pk=order_id)
        project = request.POST.get('project', '').strip()
        order.project = project
        order.save()
    return redirect('order_tree', order_id=order_id)


@login_required
def order_import(request, order_id):
    order = get_object_or_404(Order, pk=order_id)

    if request.method == 'POST' and request.FILES.get('file'):
        import openpyxl
        file = request.FILES['file']
        wb = openpyxl.load_workbook(file, data_only=True)  # читаем вычисленные значения формул
        ws = wb.active

        # ----- 1. ПОЛНАЯ ОЧИСТКА ЗАКАЗА -----
        # Сначала удаляем все экземпляры и их маршрутные карты (каскадно)
        ItemInstance.objects.filter(order=order).delete()
        # Потом удаляем старые позиции заказа (OrderItem)
        order.items.all().delete()

        # ----- 2. ОБРАБОТКА СТРОК EXCEL -----
        for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            # Читаем первые 10 столбцов (с запасом)
            designation = row[0] if len(row) > 0 else None
            name = row[1] if len(row) > 1 else None
            item_type = row[2] if len(row) > 2 else None
            qty = row[3] if len(row) > 3 else 1
            parent_desig = row[4] if len(row) > 4 else None
            material_name = row[5] if len(row) > 5 else None
            profile = row[6] if len(row) > 6 else None
            blank_size = row[7] if len(row) > 7 else None
            blanks_qty = row[8] if len(row) > 8 else None
            setup_qty = row[9] if len(row) > 9 else 0  # Настроечные детали

            if not designation:
                continue

            # ----- Обработка типа изделия (приводим к стандартному виду) -----
            if item_type:
                item_type = item_type.strip().capitalize()
            else:
                item_type = 'Деталь'

            # ----- Создаём или находим Item -----
            item, _ = Item.objects.get_or_create(
                item_number=str(designation).strip(),
                defaults={
                    'name': str(name).strip() if name else designation,
                    'item_type': item_type
                }
            )

            # ----- Сохраняем материал и заготовку -----
            if material_name and str(material_name).strip():
                mat_name = str(material_name).strip()
                # Генерируем уникальный код
                base_code = ''.join(w[0].upper() for w in mat_name.split())[:6]
                code = base_code
                counter = 1
                while Material.objects.filter(code=code).exists():
                    code = f"{base_code}_{counter}"
                    counter += 1
                mat, _ = Material.objects.get_or_create(
                    name=mat_name,
                    defaults={'code': code}
                )
                item.material = mat
            # Сохраняем профиль в Item, если он передан
            if str(profile).strip():
                item.profile = str(profile).strip()
            item.save()

            if blank_size and str(blank_size).strip():
                item.blank_size = str(blank_size).strip()
            if blanks_qty and str(blanks_qty).strip():
                try:
                    item.blanks_per_item = int(blanks_qty)
                except (ValueError, TypeError):
                    pass
            # Настроечные (буфер)
            try:
                setup_qty = int(setup_qty) if setup_qty else 0
            except (ValueError, TypeError):
                setup_qty = 0
            item.save()

            # ----- Определяем родительскую позицию -----
            parent = None
            if parent_desig and str(parent_desig).strip():
                # Ищем родительский OrderItem среди уже созданных (он должен быть выше по строкам)
                candidates = OrderItem.objects.filter(
                    order=order,
                    item__item_number=str(parent_desig).strip()
                ).order_by('-id')
                if candidates.exists():
                    parent = candidates.first()

            # ----- Создаём позицию заказа (OrderItem) -----
            oi = OrderItem.objects.create(
                order=order,
                item=item,
                quantity=int(qty) if qty else 1,
                parent=parent
            )

            # ----- Создаём экземпляр и маршрутную карту для производимых типов -----
            if item_type in ('Сборочная единица', 'Деталь'):
                # Формируем серийный номер
                serial = f"{order.order_number}-{item.item_number}-{oi.id}"
                # Проверяем уникальность (на случай, если такой номер уже есть)
                counter = 1
                base_serial = serial
                while ItemInstance.objects.filter(serial=serial).exists():
                    serial = f"{base_serial}-{counter}"
                    counter += 1
                # Создаём экземпляр
                # Сохраняем размер заготовки и количество заготовок в экземпляр
                inst_blank_size = str(blank_size).strip() if blank_size else ''
                try:
                    inst_blanks_qty = int(blanks_qty) if blanks_qty else 1
                except (ValueError, TypeError):
                    inst_blanks_qty = 1

                inst = ItemInstance.objects.create(
                    item=item,
                    serial=serial,
                    quantity=oi.quantity,
                    setup_quantity=setup_qty,
                    blank_size=inst_blank_size,
                    blanks_per_item=inst_blanks_qty,
                    order=order,
                    order_item=oi
                )
                # Создаём пустую маршрутную карту
                                # Попробуем скопировать операции из предыдущей карты для этой детали
                previous_card = RouteCard.objects.filter(
                    instance__item=item
                ).exclude(instance=inst).order_by('-instance__created_at').first()
                new_card = RouteCard.objects.create(instance=inst)
                if previous_card:
                    for op in previous_card.operations.all():
                        # Копируем операцию, сбрасывая статус и фактические данные
                        RouteOperation.objects.create(
                            route_card=new_card,
                            operation_type=op.operation_type,
                            order=op.order,
                            planned_hours=op.planned_hours,
                            status='pending',
                            good_qty=0,
                            bad_qty=0
                        )
                    # Не копируем worker, started_at, completed_at – они будут заполнены при выполнении


        messages.success(request, f'Спецификация загружена. Создано позиций: {order.items.count()}.')

    return redirect('order_tree', order_id=order.id)


@login_required
def warehouse_print_report(request):
    """Экспорт остатков основного/межоперационного склада в Excel с учётом поиска"""
    tab = request.GET.get('tab', 'main')
    search = request.GET.get('search', '').strip()

    instances = ItemInstance.objects.select_related('item', 'order').all()
    data = []

    for inst in instances:
        # Вычисляем остатки
        total_in_main = inst.warehouse_records.filter(movement_type='in_main').aggregate(s=models.Sum('quantity'))['s'] or 0
        total_out_main = inst.warehouse_records.filter(movement_type='out_main').aggregate(s=models.Sum('quantity'))['s'] or 0
        balance_main = total_in_main - total_out_main

        total_in_inter = inst.warehouse_records.filter(movement_type='in_intermediate').aggregate(s=models.Sum('quantity'))['s'] or 0
        total_out_inter = inst.warehouse_records.filter(movement_type='out_intermediate').aggregate(s=models.Sum('quantity'))['s'] or 0
        balance_inter = total_in_inter - total_out_inter

        order_number = inst.order.order_number if inst.order else ''

        # Головная сборка и подсборка
        root_name = ''
        parent_name = ''
        if inst.order_item:
            current = inst.order_item
            while current.parent:
                current = current.parent
            root_name = f"{current.item.item_number} – {current.item.name}"
            if inst.order_item.parent:
                p = inst.order_item.parent
                parent_name = f"{p.item.item_number} – {p.item.name}"

        # Место хранения
        location_main = ''
        for rec in inst.warehouse_records.filter(movement_type='in_main').order_by('-date'):
            if rec.notes:
                location_main = rec.notes
                break

        location_inter = ''
        for rec in inst.warehouse_records.filter(movement_type='in_intermediate').order_by('-date'):
            if rec.notes:
                location_inter = rec.notes
                break

        if tab == 'main' and balance_main > 0:
            entry = {
                'designation': inst.item.item_number,
                'name': inst.item.name,
                'order': order_number,
                'assembly': root_name,
                'parent_assembly': parent_name,
                'balance': balance_main,
                'location': location_main,
            }
            data.append(entry)
        elif tab == 'intermediate' and balance_inter > 0:
            entry = {
                'designation': inst.item.item_number,
                'name': inst.item.name,
                'order': order_number,
                'assembly': root_name,
                'parent_assembly': parent_name,
                'balance': balance_inter,
                'location': location_inter,
            }
            data.append(entry)

    # Фильтрация по поиску (по всем текстовым полям)
    if search:
        data = [d for d in data if
            search.lower() in (d['designation'] + d['name'] + d['order'] + d['assembly'] + d['parent_assembly'] + d['location']).lower()
        ]

    # Формируем Excel
    from openpyxl import Workbook
    from openpyxl.styles import Font, Border, Side, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = 'Остатки на складе'

    # Заголовок
    ws.merge_cells('A1:G1')
    ws['A1'] = f'Остатки на {"основном" if tab == "main" else "межоперационном"} складе'
    ws['A1'].font = Font(bold=True, size=12)

    headers = ['Обозначение', 'Наименование', 'Договор', 'Главная сборка', 'Подсборка', 'Остаток', 'Место']
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='00557A', end_color='00557A', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border

    for i, entry in enumerate(data, 4):
        ws.cell(row=i, column=1, value=entry['designation']).border = thin_border
        ws.cell(row=i, column=2, value=entry['name']).border = thin_border
        ws.cell(row=i, column=3, value=entry['order']).border = thin_border
        ws.cell(row=i, column=4, value=entry['assembly']).border = thin_border
        ws.cell(row=i, column=5, value=entry['parent_assembly'] or '—').border = thin_border
        ws.cell(row=i, column=6, value=entry['balance']).border = thin_border
        ws.cell(row=i, column=7, value=entry['location'] or '—').border = thin_border

    # Автоширина
    for col_idx in range(1, len(headers) + 1):
        max_length = 0
        for row in ws.iter_rows(min_col=col_idx, max_col=col_idx, values_only=True):
            for value in row:
                if value and len(str(value)) > max_length:
                    max_length = len(str(value))
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = (max_length + 2) * 1.2

    from io import BytesIO
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(output.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="Остатки_склада.xlsx"'
    return response



@login_required
def order_material_report(request, order_id):
    """Сводная ведомость материалов на списание по заказу"""
    order = get_object_or_404(Order, pk=order_id)
    from datetime import datetime, timedelta
    
    # Параметры фильтрации по датам
    start_date = request.GET.get('start', '')
    end_date = request.GET.get('end', '')
    
    from openpyxl import Workbook
    from openpyxl.styles import Font, Border, Side, PatternFill, Alignment
    from io import BytesIO
    
    wb = Workbook()
    ws = wb.active
    ws.title = 'Списание материалов'
    
    # Заголовок
    ws.merge_cells('A1:H1')
    ws['A1'] = f'Ведомость материалов на списание — Заказ {order.order_number}'
    ws['A1'].font = Font(bold=True, size=14)
    ws['A1'].alignment = Alignment(horizontal='center')
    
    # Шапка таблицы
    headers = ['Обозначение', 'Наименование', 'Кол-во деталей', 'Материал', 'Сортамент', 'Размер заготовки', 'Кол-во заготовок', 'Общая длина (мм)']
    header_font = Font(bold=True, color='FFFFFF', size=11)
    header_fill = PatternFill(start_color='00557A', end_color='00557A', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal='center', wrap_text=True)
    
    # Собираем данные: все экземпляры заказа, у которых есть завершённая заготовительная операция
    row = 4
    rows_data = []
    
    for order_item in order.items.all():
        for inst in order_item.instances.all():
            if not hasattr(inst, 'route_card') or not inst.route_card:
                continue
            # Проверяем наличие завершённой операции раскроя/заготовки
            material_ops = ['Заготовительная', 'Плазменная резка', 'Лазерная резка', 'Гибка']
            ops_filter = inst.route_card.operations.filter(
                operation_type__name__in=material_ops,
                status='completed'
            )
            # Применяем фильтр по датам, если заданы
            if start_date:
                ops_filter = ops_filter.filter(completed_at__gte=start_date)
            if end_date:
                end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
                ops_filter = ops_filter.filter(completed_at__lt=end_dt)
            
            if not ops_filter.exists():
                continue
            
            item = inst.item
            material = item.material.name if item.material else '—'
            profile = item.profile or '—'
            blank_size = inst.get_blank_size() or '—'
            blanks_per = inst.get_blanks_per_item()
            
            rows_data.append({
                'item_number': item.item_number,
                'name': item.name,
                'quantity': inst.quantity,
                'material': material,
                'profile': profile,
                'blank_size': blank_size,
                'blanks_per': blanks_per,
            })
    
    # Сортируем по сортаменту
    rows_data.sort(key=lambda x: x['profile'])
    
    for data in rows_data:
            item_number = data['item_number']
            name = data['name']
            quantity = data['quantity']
            material = data['material']
            profile = data['profile']
            blank_size = data['blank_size']
            blanks_per = data['blanks_per']
            
            ws.cell(row=row, column=1, value=item_number).border = thin_border
            ws.cell(row=row, column=2, value=name).border = thin_border
            ws.cell(row=row, column=3, value=quantity).border = thin_border
            ws.cell(row=row, column=4, value=material).border = thin_border
            ws.cell(row=row, column=5, value=profile).border = thin_border
            ws.cell(row=row, column=6, value=blank_size).border = thin_border
            ws.cell(row=row, column=7, value=blanks_per).border = thin_border
            
            # Вычисляем общую длину, если размер — простое число
            total_length = ''
            try:
                length = float(str(blank_size).replace(',', '.'))
                total_length = length * blanks_per
            except (ValueError, TypeError):
                pass
            
            ws.cell(row=row, column=8, value=total_length if total_length else '—').border = thin_border
            
            row += 1
    
    # Автоширина
    for col_idx in range(1, len(headers) + 1):
        max_length = 0
        for r in ws.iter_rows(min_col=col_idx, max_col=col_idx, values_only=True):
            for value in r:
                if value and len(str(value)) > max_length:
                    max_length = len(str(value))
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = (max_length + 2) * 1.1
    
    # Итоговая строка
    
    
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    response = HttpResponse(output.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Списание_материалов_Заказ_{order.order_number}.xlsx"'
    return response






@login_required
def warehouse_bulk_issue(request):
    """Массовая выдача со склада с формированием накладной"""
    if request.method != 'POST':
        return redirect('warehouse')
    
    import json
    from openpyxl import Workbook
    from openpyxl.styles import Font, Border, Side, PatternFill, Alignment
    from io import BytesIO
    
    items_json = request.POST.get('items', '[]')
    recipient = request.POST.get('recipient', '').strip()
    basis = request.POST.get('basis', '').strip()
    notes = request.POST.get('notes', '').strip()
    tab = request.POST.get('tab', 'main')
    
    if not recipient:
        messages.error(request, 'Укажите получателя.')
        return redirect('warehouse')
    
    movement_type = 'out_main' if tab == 'main' else 'out_intermediate'
    
    try:
        items = json.loads(items_json)
    except json.JSONDecodeError:
        messages.error(request, 'Некорректные данные.')
        return redirect('warehouse')
    
    issued_items = []
    errors = []
    assemblies = set()
    
    for item in items:
        instance_id = item.get('instance_id')
        quantity = item.get('quantity', 0)
        try:
            quantity = int(quantity)
        except (ValueError, TypeError):
            quantity = 0
        if quantity <= 0:
            continue
        
        inst = ItemInstance.objects.filter(pk=instance_id).first()
        if not inst:
            errors.append(f'Экземпляр {instance_id} не найден')
            continue
        
        if tab == 'main':
            total_in = inst.warehouse_records.filter(movement_type='in_main').aggregate(s=models.Sum('quantity'))['s'] or 0
            total_out = inst.warehouse_records.filter(movement_type='out_main').aggregate(s=models.Sum('quantity'))['s'] or 0
            balance = total_in - total_out
        else:
            total_in = inst.warehouse_records.filter(movement_type='in_intermediate').aggregate(s=models.Sum('quantity'))['s'] or 0
            total_out = inst.warehouse_records.filter(movement_type='out_intermediate').aggregate(s=models.Sum('quantity'))['s'] or 0
            balance = total_in - total_out
        
        if quantity > balance:
            errors.append(f'Недостаточно остатка для {inst.item.item_number} (остаток: {balance})')
            continue
        
        WarehouseRecord.objects.create(
            instance=inst,
            movement_type=movement_type,
            quantity=quantity,
            recipient=recipient,
            basis=basis,
            notes=notes,
            employee=request.user.employee if hasattr(request.user, 'employee') else None
        )
        
        root_name = ''
        parent_name = ''
        if inst.order_item:
            current = inst.order_item
            while current.parent:
                current = current.parent
            root_name = f"{current.item.item_number} – {current.item.name}"
            if inst.order_item.parent:
                p = inst.order_item.parent
                parent_name = f"{p.item.item_number} – {p.item.name}"
            assemblies.add(root_name)
        
        issued_items.append({
            'designation': inst.item.item_number,
            'name': inst.item.name,
            'assembly': root_name,
            'subassembly': parent_name,
            'quantity': quantity
        })
    
    if errors:
        for err in errors:
            messages.warning(request, err)
    if not issued_items:
        messages.error(request, 'Нет позиций для выдачи.')
        return redirect('warehouse')
    
    if not basis and assemblies:
        basis = ', '.join(sorted(assemblies))
    
    # --- Формирование Excel ---
    wb = Workbook()
    ws = wb.active
    ws.title = 'Накладная на выдачу'
    
    # Заголовок
    ws.merge_cells('A1:E1')
    ws['A1'] = f'Накладная на выдачу со склада ({ "основной" if tab == "main" else "межоперационный" })'
    ws['A1'].font = Font(bold=True, size=14)
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 30
    
    # Единый шрифт 10pt для всех данных
    info_font = Font(size=10)
    info_align = Alignment(vertical='center', wrap_text=True)
    
    # Информационный блок
    ws['A3'] = 'Получатель:'
    ws['A3'].font = info_font; ws['A3'].alignment = info_align
    ws['B3'] = recipient
    ws['B3'].font = info_font; ws['B3'].alignment = info_align
    ws.row_dimensions[3].height = 35
    
    ws['A4'] = 'Основание:'
    ws['A4'].font = info_font; ws['A4'].alignment = info_align
    ws['B4'] = basis
    ws['B4'].font = info_font; ws['B4'].alignment = info_align
    ws.row_dimensions[4].height = 35
    
    issued_by = request.user.get_full_name() or request.user.username
    ws['A6'] = 'Выдал:'
    ws['A6'].font = info_font; ws['A6'].alignment = info_align
    ws['B6'] = issued_by
    ws['B6'].font = info_font; ws['B6'].alignment = info_align
    ws.row_dimensions[6].height = 35
    
    msk_time = (timezone.now() + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
    ws['A7'] = 'Дата:'
    ws['A7'].font = info_font; ws['A7'].alignment = info_align
    ws['B7'] = msk_time
    ws['B7'].font = info_font; ws['B7'].alignment = info_align
    ws.row_dimensions[7].height = 35
    
    # Шапка таблицы
    headers = ['Обозначение', 'Наименование', 'Главная сборка', 'Подсборка', 'Количество']
    header_font = Font(bold=True, color='FFFFFF', size=10)
    header_fill = PatternFill(start_color='00557A', end_color='00557A', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    data_align = Alignment(vertical='top', wrap_text=True)
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=9, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[9].height = 22
    
    # Строки данных
    for i, item in enumerate(issued_items, 10):
        ws.cell(row=i, column=1, value=item['designation']).border = thin_border
        ws.cell(row=i, column=2, value=item['name']).border = thin_border
        ws.cell(row=i, column=3, value=item['assembly']).border = thin_border
        ws.cell(row=i, column=4, value=item['subassembly'] or '—').border = thin_border
        ws.cell(row=i, column=5, value=item['quantity']).border = thin_border
        for c in range(1, 6):
            ws.cell(row=i, column=c).alignment = data_align
            ws.cell(row=i, column=c).font = Font(size=10)
        ws.row_dimensions[i].height = 35  # высота побольше для читаемости
    
    # Ширина столбцов
    col_widths = {'A': 22, 'B': 32, 'C': 32, 'D': 26, 'E': 12}
    for letter, width in col_widths.items():
        ws.column_dimensions[letter].width = width
    
    # Параметры страницы
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = 9
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = openpyxl.worksheet.properties.PageSetupProperties(fitToPage=True)
    
    # Подписи
    row_num = 10 + len(issued_items) + 1
    ws.cell(row=row_num, column=1, value='Выдал: _________________________').font = Font(size=10)
    ws.cell(row=row_num + 1, column=1, value='(подпись, расшифровка)').font = Font(size=9, italic=True)
    ws.cell(row=row_num, column=3, value='Получил: _________________________').font = Font(size=10)
    ws.cell(row=row_num + 1, column=3, value='(подпись, расшифровка)').font = Font(size=9, italic=True)
    
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    response = HttpResponse(output.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Накладная_{recipient}_{timezone.now().strftime("%Y%m%d_%H%M")}.xlsx"'
    messages.success(request, f'Выдано {len(issued_items)} позиций. Накладная сформирована.')
    return response



@login_required
def update_storage_location(request):
    """Обновление места хранения на складе"""
    if request.method != 'POST':
        return redirect('warehouse')
    
    instance_id = request.POST.get('instance_id')
    location = request.POST.get('location', '').strip()
    tab = request.POST.get('tab', 'main')
    movement_type = 'in_main' if tab == 'main' else 'in_intermediate'
    
    if not instance_id:
        messages.error(request, 'Экземпляр не указан')
        return redirect('warehouse')
    
    inst = ItemInstance.objects.filter(pk=instance_id).first()
    if not inst:
        messages.error(request, 'Экземпляр не найден')
        return redirect('warehouse')
    
    # Создаём новую запись в журнале для изменения места
    WarehouseRecord.objects.create(
        instance=inst,
        movement_type=movement_type,
        quantity=0,
        notes=f'Изменение места хранения: {location}',
        employee=request.user.employee if hasattr(request.user, 'employee') else None
    )
    messages.success(request, f'Место хранения обновлено для {inst.item.item_number}')
    return redirect('warehouse')


@login_required
def warehouse_dashboard(request):
    # Оптимизированная версия: агрегируем остатки и места одним запросом
    from django.db.models import Sum, Q, OuterRef, Subquery

    # Агрегация остатков и мест хранения
    instances_with_balance = ItemInstance.objects.annotate(
        total_in_main=Sum('warehouse_records__quantity',
                         filter=Q(warehouse_records__movement_type='in_main')),
        total_out_main=Sum('warehouse_records__quantity',
                          filter=Q(warehouse_records__movement_type='out_main')),
        total_in_inter=Sum('warehouse_records__quantity',
                          filter=Q(warehouse_records__movement_type='in_intermediate')),
        total_out_inter=Sum('warehouse_records__quantity',
                           filter=Q(warehouse_records__movement_type='out_intermediate')),
        latest_main_note=Subquery(
            WarehouseRecord.objects.filter(
                instance=OuterRef('pk'),
                movement_type='in_main',
                notes__gt=''
            ).order_by('-date').values('notes')[:1]
        ),
        latest_inter_note=Subquery(
            WarehouseRecord.objects.filter(
                instance=OuterRef('pk'),
                movement_type='in_intermediate',
                notes__gt=''
            ).order_by('-date').values('notes')[:1]
        )
    ).select_related('item', 'order', 'order_item__parent__item')

    main_data = []
    intermediate_data = []
    employee_list = Employee.objects.filter(is_active=True)

    for inst in instances_with_balance:
        balance_main = (inst.total_in_main or 0) - (inst.total_out_main or 0)
        balance_inter = (inst.total_in_inter or 0) - (inst.total_out_inter or 0)

        if balance_main <= 0 and balance_inter <= 0:
            continue

        order_number = inst.order.order_number if inst.order else ''
        root_name = ''
        parent_name = ''
        if inst.order_item:
            current = inst.order_item
            while current.parent:
                current = current.parent
            root_name = f"{current.item.item_number} – {current.item.name}"
            if inst.order_item.parent:
                p = inst.order_item.parent
                parent_name = f"{p.item.item_number} – {p.item.name}"

        location_main = inst.latest_main_note or ''
        location_inter = inst.latest_inter_note or ''

        if balance_main > 0:
            main_data.append({
                'instance': inst,
                'balance': balance_main,
                'assembly': root_name,
                'order_number': order_number,
                'location': location_main,
                'parent_assembly': parent_name,
            })
        if balance_inter > 0:
            intermediate_data.append({
                'instance': inst,
                'balance': balance_inter,
                'assembly': root_name,
                'order_number': order_number,
                'location': location_inter,
                'parent_assembly': parent_name,
            })

    # Новые поступления — сверху
    main_data.reverse()
    intermediate_data.reverse()

    # Журнал (без пагинации)
    records = WarehouseRecord.objects.select_related('instance__item', 'employee').order_by('-date')

    # Итоги
    main_total_qty = sum(item['balance'] for item in main_data)
    main_unique_items = len(set(item['instance'].item_id for item in main_data))
    inter_total_qty = sum(item['balance'] for item in intermediate_data)
    inter_unique_items = len(set(item['instance'].item_id for item in intermediate_data))

    context = {
        'main_data': main_data,
        'intermediate_data': intermediate_data,
        'records': records,
        'employee_list': employee_list,
        'main_total_qty': main_total_qty,
        'main_unique_items': main_unique_items,
        'inter_total_qty': inter_total_qty,
        'inter_unique_items': inter_unique_items,
    }

    user_role = request.user.employee.role if hasattr(request.user, 'employee') else ''
    for item in context['main_data']:
        item['can_issue'] = user_role in ['admin', 'storekeeper', 'master', 'dispatcher']
        item['can_edit_location'] = item['can_issue']
    for item in context['intermediate_data']:
        item['can_issue'] = user_role in ['admin', 'storekeeper', 'master', 'dispatcher']
        item['can_edit_location'] = item['can_issue']

    return render(request, 'scanner/warehouse_dashboard.html', context)
# --- Статистика ---

def get_working_hours(start_dt, end_dt):
    """Расчёт рабочих часов между двумя датами (пн-пт, 7:00–23:30)"""
    from datetime import timedelta, datetime
    if not start_dt or not end_dt:
        return 0
    if start_dt >= end_dt:
        return 0
    
    work_start_hour = 7
    work_end_hour = 23.5  # 23:30
    
    total_hours = 0
    current = start_dt
    
    while current < end_dt:
        # Проверяем, рабочий ли день (пн-пт)
        if current.weekday() < 5:  # 0=пн, 4=пт
            # Определяем начало и конец рабочего дня для текущей даты
            day_start = current.replace(hour=work_start_hour, minute=0, second=0, microsecond=0)
            day_end = current.replace(hour=int(work_end_hour), minute=30, second=0, microsecond=0)
            
            # Определяем фактическое начало работы для этой даты
            actual_start = max(current, day_start)
            actual_end = min(end_dt, day_end)
            
            if actual_start < actual_end:
                delta = (actual_end - actual_start).total_seconds() / 3600
                total_hours += delta
        
        # Переходим к следующему дню (на начало следующих суток)
        current = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    return total_hours


@login_required
def statistics(request):
    from django.db.models import Count, Prefetch, Q, Sum
    from django.db.models.functions import TruncDate
    from datetime import datetime, timedelta

    # Параметры фильтрации
    start_date = request.GET.get('start', '')
    end_date = request.GET.get('end', '')

    # Базовые запросы
    ops = RouteOperation.objects.select_related('operation_type', 'worker')
    wh = WarehouseRecord.objects.select_related('instance__item', 'employee')

    if start_date and start_date != 'None':
        ops = ops.filter(completed_at__gte=start_date)
        wh = wh.filter(date__gte=start_date)
    if end_date and end_date != 'None':
        # end_date включительно до конца дня
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        ops = ops.filter(completed_at__lt=end_dt)
        wh = wh.filter(date__lt=end_dt)

    # Суммарные показатели
    total_ops = ops.filter(status='completed').count()
    # Годные = сумма good_qty по всем завершённым операциям (аналогично браку)
    total_good = ops.filter(status='completed').aggregate(s=Sum('good_qty'))['s'] or 0
    
    # Деталей в работе (партии с незавершёнными операциями)
    instances_in_progress = ItemInstance.objects.filter(
        route_card__operations__status__in=['in_progress', 'pending']
    ).distinct().count()

    # Сохраняем прежний состав показателя и переход в детализацию брака.
    total_bad = ops.aggregate(s=Sum('bad_qty'))['s'] or 0

    # По типам операций — один агрегирующий запрос вместо серии запросов по каждому типу.
    op_stats = [
        {
            'name': row['operation_type__name'],
            'count': row['count'],
            'good': row['good'] or 0,
            'bad': row['bad'] or 0,
        }
        for row in ops
        .values('operation_type__name')
        .annotate(count=Count('id', filter=Q(status='completed')), good=Sum('good_qty'), bad=Sum('bad_qty'))
        .filter(count__gt=0)
        .order_by('-count', 'operation_type__name')
    ]

    # Складские показатели: простая сумма quantity по всем записям за период
    in_main = wh.filter(movement_type='in_main').aggregate(s=Sum('quantity'))['s'] or 0
    in_inter = wh.filter(movement_type='in_intermediate').aggregate(s=Sum('quantity'))['s'] or 0
    out_main = wh.filter(movement_type='out_main').aggregate(s=Sum('quantity'))['s'] or 0
    out_inter = wh.filter(movement_type='out_intermediate').aggregate(s=Sum('quantity'))['s'] or 0

    # График использует выбранный период. Без фильтра показываем последние 30 дней.
    chart_ops = ops.filter(status='completed')
    chart_period_label = 'за выбранный период'
    if not start_date and not end_date:
        chart_ops = chart_ops.filter(completed_at__gte=timezone.now() - timedelta(days=30))
        chart_period_label = 'за последние 30 дней'

    daily_rows = chart_ops.annotate(day=TruncDate('completed_at')).values('day').annotate(
        good=Sum('good_qty'), bad=Sum('bad_qty'), count=Count('id')
    ).order_by('day')
    daily_ops = [
        {
            'day': row['day'].strftime('%d.%m') if row['day'] else '',
            'good': row['good'] or 0,
            'bad': row['bad'] or 0,
            'count': row['count'],
        }
        for row in daily_rows
    ]


    context = {

        'start_date': start_date,
        'end_date': end_date,
        'total_ops': total_ops,
        'instances_in_progress': instances_in_progress,
        'total_good': total_good,
        'total_bad': total_bad,
        'op_stats': op_stats,
        'in_main': in_main,
        'in_inter': in_inter,
        'out_main': out_main,
        'out_inter': out_inter,
        'daily_ops': list(daily_ops),
        'op_types_json': [{'name': s['name'], 'count': s['count']} for s in op_stats],
        'chart_period_label': chart_period_label,
    }

    # --- Долго в работе ---
    # Оставляем только реально начатые операции старше 24 рабочих часов.
    all_in_progress = RouteOperation.objects.filter(
        status='in_progress'
    ).select_related(
        'route_card__instance__item',
        'route_card__instance__order',
        'worker__employee',
        'operation_type',
    ).order_by('started_at')
    
    # Фильтруем по рабочим часам
    stuck_in_progress_raw = []
    now = timezone.now()
    for op in all_in_progress:
        if op.started_at:
            working_hours = get_working_hours(op.started_at, now)
            if working_hours > 24:
                stuck_in_progress_raw.append(op)
    
    # Одна самая старая операция на экземпляр.
    stuck_in_progress = []
    seen_instances = set()
    for op in stuck_in_progress_raw:
        inst_id = op.route_card.instance_id
        if inst_id not in seen_instances:
            seen_instances.add(inst_id)
            stuck_in_progress.append({
                'operation': op,
                'working_hours': round(get_working_hours(op.started_at, now)),
                'order_number': op.route_card.instance.order.order_number if op.route_card.instance.order else '—',
            })

    stuck_in_progress.sort(key=lambda item: item['working_hours'], reverse=True)
    stuck_in_progress_count = len(stuck_in_progress)
    stuck_in_progress = stuck_in_progress[:30]
    
    # --- Готово к сборке ---
    ready_for_assembly = []
    assembly_instances = ItemInstance.objects.filter(
        item__item_type='Сборочная единица',
        route_card__isnull=False
    ).select_related(
        'item',
        'order',
        'route_card',
        'order_item__item',
        'order_item__parent__item',
        'order_item__parent__parent__item',
        'order_item__parent__parent__parent__item',
    ).prefetch_related(
        'route_card__operations',
        'order_item__children__item',
        Prefetch(
            'order_item__children__instances',
            queryset=ItemInstance.objects.select_related('route_card').prefetch_related('route_card__operations'),
        ),
    )

    for inst in assembly_instances:
        if not inst.all_components_ready():
            continue

        assembly_ops = list(inst.route_card.operations.all())
        has_in_progress = any(op.status == 'in_progress' for op in assembly_ops)
        has_pending = any(op.status == 'pending' for op in assembly_ops)
        has_completed = any(op.status == 'completed' for op in assembly_ops)
        is_ready = not assembly_ops or (has_pending and not has_completed and not has_in_progress)

        if is_ready:
            # Определяем главную сборку
            root_name = ''
            if inst.order_item:
                current = inst.order_item
                while current.parent:
                    current = current.parent
                root_name = f"{current.item.item_number} – {current.item.name}"
            
            # Определяем подсборку (родительскую позицию)
            parent_name = ''
            if inst.order_item and inst.order_item.parent:
                p = inst.order_item.parent
                parent_name = f"{p.item.item_number} – {p.item.name}"

            ready_for_assembly.append({
                'item_number': inst.item.item_number,
                'name': inst.item.name,
                'serial': inst.serial,
                'assembly': root_name,
                'parent_assembly': parent_name,
                'order_number': inst.order.order_number if inst.order else '—',
                'quantity': inst.quantity,
                'due_date': inst.due_date or (inst.order.due_date if inst.order else None),
            })

    ready_for_assembly.sort(key=lambda item: (
        item['due_date'] is None,
        item['due_date'] or date.max,
        item['order_number'],
        item['item_number'],
    ))

    context['stuck_in_progress'] = stuck_in_progress
    context['stuck_in_progress_count'] = stuck_in_progress_count
    context['ready_for_assembly'] = ready_for_assembly
    context['ready_for_assembly_count'] = len(ready_for_assembly)

    return render(request, 'scanner/statistics.html', context)


def logout_view(request):
    logout(request)
    return redirect('/accounts/login/')



@login_required
def operations_planning(request):
    from scanner.models import Order, OperationType
    """Планирование операций: текущие и следующие операции по всем заказам"""
    from django.db.models import Min, Q
    
    # Фильтры из GET-параметров
    order_id = request.GET.get('order', '')
    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')
    
    # Все экземпляры с маршрутными картами (оптимизировано)
    instances = ItemInstance.objects.filter(
        route_card__isnull=False
    ).select_related('item', 'order', 'route_card').prefetch_related(
        'route_card__operations__operation_type',
        'route_card__operations__worker__employee'
    )
    
    # Применяем фильтры
    if order_id:
        instances = instances.filter(order_id=order_id)
    
    planning_data = []
    
    for inst in instances:
        if not inst.route_card:
            continue
        ops = inst.route_card.operations.select_related('operation_type', 'worker__employee').order_by('order')
        if not ops.exists():
            continue
        
        # Находим текущую операцию (в работе или первую ожидающую)
        current_op = None
        next_op = None
        found_current = False
        
        for op in ops:
            if op.status == 'in_progress':
                current_op = op
                found_current = True
            elif op.status == 'pending' and not found_current:
                # Для операции "Комплектование" проверяем готовность компонентов
                if op.operation_type.name == 'Комплектование' and not inst.all_components_ready():
                    # Если компоненты не готовы, пропускаем эту сборку
                    current_op = None
                    found_current = True
                else:
                    current_op = op
                    found_current = True
            elif found_current and op.status == 'pending' and next_op is None:
                next_op = op
                break
        
        if not current_op:
            continue
        
        # Фильтр по статусу текущей операции
        if status_filter and current_op.status != status_filter:
            continue
        
        # Фильтр по типу операции
        if type_filter and current_op.operation_type.name != type_filter:
            continue
        
        planning_data.append({
            'instance': inst,
            'current_op': current_op,
            'next_op': next_op,
        })
    
    # Список заказов для фильтра
    orders = Order.objects.filter(status__in=['draft', 'in_progress', 'paused']).order_by('full_name', 'order_number')
    # Список типов операций для фильтра
    op_types = OperationType.objects.all().order_by('name')
    

    context = {

        'planning_data': planning_data,
        'orders': orders,
        'op_types': op_types,
        'selected_order': order_id,
        'selected_status': status_filter,
        'selected_type': type_filter,
    }
    
    return render(request, 'scanner/operations_planning.html', context)



@login_required
def statistics_bad_operations(request):
    """Список операций с браком за период"""
    from django.core.paginator import Paginator
    from datetime import datetime, timedelta

    start_date = request.GET.get('start', '')
    end_date = request.GET.get('end', '')
    order_id = request.GET.get('order', '')
    type_name = request.GET.get('type', '')

    ops = RouteOperation.objects.filter(bad_qty__gt=0)\
        .select_related('operation_type', 'worker__employee', 'route_card__instance__item', 'route_card__instance__order')

    if start_date and start_date != 'None':
        ops = ops.filter(completed_at__gte=start_date)
    if end_date and end_date != 'None':
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        ops = ops.filter(completed_at__lt=end_dt)
    if order_id:
        ops = ops.filter(route_card__instance__order_id=order_id)
    if type_name:
        ops = ops.filter(operation_type__name=type_name)

    ops = ops.extra(select={'sort_date': "COALESCE(completed_at, started_at)"}).order_by('-sort_date')

    # Убираем дубли (один экземпляр – одна запись с суммой брака?)
    # Группируем по операции и экземпляру
    seen = set()
    unique_ops = []
    for op in ops:
        key = (op.route_card.instance_id, op.operation_type_id)
        if key not in seen:
            seen.add(key)
            unique_ops.append(op)

    paginator = Paginator(unique_ops, 50)
    page = request.GET.get('page', 1)
    ops_page = paginator.get_page(page)

    orders = Order.objects.filter(status__in=['draft', 'in_progress', 'paused', 'completed', 'shipped']).order_by('order_number')
    op_types = OperationType.objects.all().order_by('name')


    context = {

        'ops': ops_page,
        'start_date': start_date,
        'end_date': end_date,
        'selected_order': order_id,
        'selected_type': type_name,
        'orders': orders,
        'op_types': op_types,
        'total_bad': sum(op.bad_qty for op in unique_ops),
    }
    return render(request, 'scanner/statistics_bad_operations.html', context)




@login_required
def worker_operations(request, username):
    """Список операций конкретного сотрудника (только для администратора)"""
    if not request.user.is_superuser and (not hasattr(request.user, 'employee') or request.user.employee.role != 'admin'):
        messages.error(request, 'Доступ запрещён.')
        return redirect('home')
    
    from django.contrib.auth.models import User
    from datetime import datetime, timedelta
    from django.core.paginator import Paginator
    from django.db.models import Sum
    
    worker = get_object_or_404(User, username=username)
    
    start_date = request.GET.get('start', '')
    end_date = request.GET.get('end', '')
    
    ops = RouteOperation.objects.filter(worker=worker).select_related('operation_type', 'route_card__instance__item', 'route_card__instance__order')
    
    if start_date:
        ops = ops.filter(completed_at__gte=start_date)
    if end_date:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        ops = ops.filter(completed_at__lt=end_dt)
    
    ops = ops.order_by('-completed_at')
    
    # Пагинация
    paginator = Paginator(ops, 50)
    page = request.GET.get('page', 1)
    ops_page = paginator.get_page(page)
    
    total_good = ops.aggregate(s=Sum('good_qty'))['s'] or 0
    total_bad = ops.aggregate(s=Sum('bad_qty'))['s'] or 0
    
    return render(request, 'scanner/worker_operations.html', {
        'worker': worker,
        'ops': ops_page,
        'total_good': total_good,
        'total_bad': total_bad,
        'start_date': start_date,
        'end_date': end_date,
    })


@login_required
def worker_stats(request):
    """Статистика по сотрудникам (только для администратора)"""
    if not request.user.is_superuser and (not hasattr(request.user, 'employee') or request.user.employee.role != 'admin'):
        messages.error(request, 'Доступ запрещён.')
        return redirect('home')
    
    from django.db.models import Count, Sum, Q
    from datetime import timedelta
    
    # Фильтр по датам
    start_date = request.GET.get('start', '')
    end_date = request.GET.get('end', '')
    
    ops = RouteOperation.objects.filter(status__in=['in_progress', 'completed'], worker__isnull=False)
    
    if start_date:
        ops = ops.filter(completed_at__gte=start_date)
    if end_date:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        ops = ops.filter(completed_at__lt=end_dt)
    
    # Группировка по сотруднику
    stats = ops.values('worker__username', 'worker__first_name', 'worker__last_name').annotate(
        total_ops=Count('id'),
        good_qty=Sum('good_qty'),
        bad_qty=Sum('bad_qty'),
    ).order_by('-total_ops')
    
    # Добавляем ФИО и время работы
    worker_stats = []
    for s in stats:
        worker_ops = ops.filter(worker__username=s['worker__username'])
        total_seconds = 0
        for op in worker_ops:
            if op.started_at and op.completed_at:
                total_seconds += (op.completed_at - op.started_at).total_seconds()
        hours = round(total_seconds / 3600, 1)
        
        # Получаем ФИО из Employee
        try:
            from scanner.models import Employee
            emp = Employee.objects.filter(user__username=s['worker__username']).first()
            full_name = str(emp) if emp else (f"{s['worker__last_name']} {s['worker__first_name']}".strip() or s['worker__username'])
        except:
            full_name = f"{s['worker__last_name']} {s['worker__first_name']}".strip() or s['worker__username']
        
        worker_stats.append({
            'username': s['worker__username'],
            'full_name': full_name,
            'total_ops': s['total_ops'],
            'good_qty': s['good_qty'] or 0,
            'bad_qty': s['bad_qty'] or 0,
            'hours': hours,
        })
    
    return render(request, 'scanner/worker_stats.html', {
        'worker_stats': worker_stats,
        'start_date': start_date,
        'end_date': end_date,
    })


@login_required
def statistics_operations(request, type_name):
    from django.db.models import Q,  Count, Max, Q, F, Count, Max, Q, F, Count, Max, Q, F, Count, Max, Q, F, Sum
    from datetime import datetime, timedelta, date

    start_date = request.GET.get('start')
    end_date = request.GET.get('end')

    ops = RouteOperation.objects.select_related('operation_type', 'worker', 'route_card__instance__item', 'route_card__instance__order').filter(
        operation_type__name=type_name,
        status__in=['in_progress', 'completed']
    ).extra(select={'sort_date': "COALESCE(completed_at, started_at)"}).order_by('-sort_date')

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
    from datetime import datetime, timedelta, date
    from django.db.models import Q,  Count, Max, Q, F, Count, Max, Q, F, Count, Max, Q, F, Count, Max, Q, F, Sum

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
    from datetime import datetime, timedelta, date
    from django.db.models import Q,  Count, Max, Q, F, Count, Max, Q, F, Count, Max, Q, F, Count, Max, Q, F, Sum

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
    # Годные и брак — по всем операциям (включая незавершённые)
    total_good = ops.aggregate(s=Sum('good_qty'))['s'] or 0
    total_bad = ops.aggregate(s=Sum('bad_qty'))['s'] or 0
    main_instance_ids = wh.filter(movement_type='in_main').values_list('instance_id', flat=True).distinct()
    completed_main_ids = ItemInstance.objects.filter(id__in=main_instance_ids, route_card__operations__status='completed').annotate(total_ops=Count('route_card__operations'), completed_ops=Count('route_card__operations', filter=Q(route_card__operations__status='completed'))).filter(total_ops=F('completed_ops')).values_list('id', flat=True).distinct()
    in_main = ItemInstance.objects.filter(id__in=completed_main_ids).aggregate(s=Sum('quantity'))['s'] or 0
    inter_instance_ids = wh.filter(movement_type='in_intermediate').values_list('instance_id', flat=True).distinct()
    completed_inter_ids = ItemInstance.objects.filter(id__in=inter_instance_ids, route_card__operations__status='completed').annotate(total_ops=Count('route_card__operations'), completed_ops=Count('route_card__operations', filter=Q(route_card__operations__status='completed'))).filter(total_ops=F('completed_ops')).values_list('id', flat=True).distinct()
    in_inter = ItemInstance.objects.filter(id__in=completed_inter_ids).aggregate(s=Sum('quantity'))['s'] or 0
    out_main_ids = wh.filter(movement_type='out_main').values_list('instance_id', flat=True).distinct()
    completed_out_ids = ItemInstance.objects.filter(id__in=out_main_ids, route_card__operations__status='completed').annotate(total_ops=Count('route_card__operations'), completed_ops=Count('route_card__operations', filter=Q(route_card__operations__status='completed'))).filter(total_ops=F('completed_ops')).values_list('id', flat=True).distinct()
    out_main = ItemInstance.objects.filter(id__in=completed_out_ids).aggregate(s=Sum('quantity'))['s'] or 0



    # По типам операций
    op_types = OperationType.objects.all()
    op_stats = []
    for ot in op_types:
        qs = ops.filter(operation_type=ot)
        cnt = qs.filter(status='completed').count()
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
    from django.db.models import Q,  Count, Max, Q, F, Count, Max, Q, F, Count, Max, Q, F, Count, Max, Q, F, Sum
    from datetime import datetime, timedelta, date

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
        main_ids = wh.filter(movement_type='in_main').values_list('instance_id', flat=True).distinct()
        out_ids = wh.filter(movement_type='out_main').values_list('instance_id', flat=True).distinct()
        completed_main_ids = ItemInstance.objects.filter(id__in=main_ids, route_card__operations__status='completed').annotate(total_ops=Count('route_card__operations'), completed_ops=Count('route_card__operations', filter=Q(route_card__operations__status='completed'))).filter(total_ops=F('completed_ops')).values_list('id', flat=True).distinct()
        completed_out_ids = ItemInstance.objects.filter(id__in=out_ids, route_card__operations__status='completed').annotate(total_ops=Count('route_card__operations'), completed_ops=Count('route_card__operations', filter=Q(route_card__operations__status='completed'))).filter(total_ops=F('completed_ops')).values_list('id', flat=True).distinct()
        return {
            'total_ops': ops.count(),
            'total_good': ops.aggregate(s=Sum('good_qty'))['s'] or 0,
            'total_bad': ops.aggregate(s=Sum('bad_qty'))['s'] or 0,
            'in_main': ItemInstance.objects.filter(id__in=completed_main_ids).aggregate(s=Sum('quantity'))['s'] or 0,
            'out_main': ItemInstance.objects.filter(id__in=completed_out_ids).aggregate(s=Sum('quantity'))['s'] or 0,
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

@login_required
def order_create(request):
    if request.method == 'POST':
        number = request.POST.get('order_number', '').strip()
        name = request.POST.get('full_name', '').strip()
        due_date = request.POST.get('due_date', '') or None
        if number:
            # Проверяем уникальность наименования, если оно указано
            if name and Order.objects.filter(full_name=name).exists():
                messages.error(request, f'Заказ с наименованием "{name}" уже существует.')
                return redirect('home')
            Order.objects.create(order_number=number, full_name=name or None, due_date=due_date)
    return redirect('home')

import os
import glob
from django.contrib import messages

@login_required
def restore_backup(request):
    # Доступ только для администраторов (is_staff)
    if not request.user.is_staff:
        messages.error(request, 'Недостаточно прав.')
        return redirect('home')
    
    backup_dir = os.path.join(settings.BASE_DIR, 'backups')
    backups = sorted(glob.glob(os.path.join(backup_dir, 'db_*.sqlite3')), reverse=True)
    backup_files = [os.path.basename(f) for f in backups]
    
    if request.method == 'POST':
        selected = request.POST.get('backup_file')
        if selected and selected in backup_files:
            src = os.path.join(backup_dir, selected)
            dst = os.path.join(settings.BASE_DIR, 'db.sqlite3')
            import shutil
            shutil.copy2(src, dst)
            messages.success(request, 'База данных восстановлена. Сервер будет перезагружен.')
            # Перезапускаем gunicorn для применения новой базы
            import subprocess
            subprocess.run(['sudo', 'systemctl', 'restart', 'gunicorn-simple'])
        else:
            messages.error(request, 'Выберите файл из списка.')
        return redirect('restore_backup')
    
    return render(request, 'scanner/restore_backup.html', {
        'backups': backup_files,
    })



@login_required
def search(request):
    query = request.GET.get('q', '').strip()
    results = {
        'items': [],
        'orders': [],
        'instances': [],
        'operations': [],
    }
    if query:
        results['items'] = Item.objects.filter(
            Q(item_number__icontains=query) | Q(name__icontains=query)
        )
        results['orders'] = Order.objects.filter(
            Q(order_number__icontains=query) | Q(full_name__icontains=query)
        )[:10]
        results['instances'] = ItemInstance.objects.filter(
            Q(serial__icontains=query)
        ).select_related('item')
        results['operations'] = RouteOperation.objects.filter(
            Q(operation_type__name__icontains=query) |
            Q(route_card__instance__item__name__icontains=query) |
            Q(worker__username__icontains=query)
        ).select_related('operation_type', 'route_card__instance__item')[:30]
    return render(request, 'scanner/search_results.html', {
        'query': query,
        'results': results,
    })
@login_required
def orders_control(request):
    if not request.user.is_staff and (not hasattr(request.user, 'employee') or request.user.employee.role not in ['admin', 'dispatcher', 'master', 'technologist', 'supervisor']):
        messages.error(request, 'Недостаточно прав. Обратитесь к администратору.')
        return redirect('home')
    
    orders = Order.objects.all().order_by('order_number')
    today = date.today()
    
    orders_data = []
    for order in orders:
        days_left = None
        if order.due_date:
            delta = order.due_date - today
            days_left = delta.days
        
        # Прогресс выполнения заказа
        total_ops = 0
        completed_ops = 0
        for inst in ItemInstance.objects.filter(order=order, route_card__isnull=False):
            rc = inst.route_card
            total_ops += rc.operations.count()
            completed_ops += rc.operations.filter(status='completed').count()
        progress = int(completed_ops / total_ops * 100) if total_ops > 0 else 0
        
        orders_data.append({
            'order': order,
            'days_left': days_left,
            'progress': progress,
        })
    
    hide_completed = request.GET.get('hide_completed') == '1'
    if hide_completed:
        orders_data = [o for o in orders_data if o['progress'] < 100]

    return render(request, 'scanner/orders_control.html', {
        'orders_data': orders_data,
        'today': today,
        'hide_completed': hide_completed,
    })
@login_required
def change_order_status(request, order_id, new_status):
    order = get_object_or_404(Order, pk=order_id)
    # Проверяем, что у пользователя есть права (is_staff)
    if not request.user.is_staff:
        messages.error(request, 'Недостаточно прав для изменения статуса заказа.')
        return redirect('order_detail', order_id=order.id)
    # Проверяем, что новый статус допустим
    if new_status not in dict(Order.STATUS_CHOICES):
        messages.error(request, 'Недопустимый статус.')
        return redirect('order_detail', order_id=order.id)
    order.status = new_status
    order.save()
    messages.success(request, f'Статус заказа изменён на {order.get_status_display()}.')
    return redirect('order_detail', order_id=order.id)

@login_required
def update_order_due_date(request, order_id):
    if not request.user.is_staff:
        messages.error(request, 'Недостаточно прав.')
        return redirect('home')
    order = get_object_or_404(Order, pk=order_id)
    if request.method == 'POST':
        due_date = request.POST.get('due_date')
        if due_date:
            order.due_date = due_date
        else:
            order.due_date = None
        order.save()
        messages.success(request, f'Дата отгрузки заказа {order.order_number} обновлена.')
    return redirect('home')

import tempfile
import zipfile
import os

@login_required
def download_all_route_cards(request, order_id):
    if not request.user.is_staff:
        messages.error(request, 'Недостаточно прав.')
        return redirect('order_tree', order_id=order_id)

    order = get_object_or_404(Order, pk=order_id)
    instances = ItemInstance.objects.filter(order=order).select_related('item')

    # Создаём временную папку для Excel-файлов
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, 'ml_files.zip')
        with zipfile.ZipFile(zip_path, 'w') as zf:
            for inst in instances:
                if not inst.route_card:
                    continue
                # Получаем Excel-файл через route_card_print
                # Передаём request, чтобы работал build_absolute_uri
                response = route_card_print(request, inst.route_card.id)
                if response.status_code == 200:
                    # Имя файла внутри архива
                    filename = f"{inst.item.item_number}_{inst.serial}.xlsx"
                    # Сохраняем в zip
                    zf.writestr(filename, response.content)

        # Отдаём архив
        with open(zip_path, 'rb') as f:
            archive_data = f.read()

    response = HttpResponse(archive_data, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="маршрутные_карты_заказ_{order.id}.zip"'
    return response

@login_required
def save_order_colors(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    if not request.user.is_staff:
        messages.error(request, 'Недостаточно прав.')
        return redirect('order_detail', order_id=order.id)
    if request.method == 'POST':
        color1 = request.POST.get('color1', '#ffffff')
        color2 = request.POST.get('color2', '#ffffff')
        order.color1 = color1
        order.color2 = color2
        order.save()
        messages.success(request, 'Цвета сохранены.')
    return redirect('order_detail', order_id=order.id)

import openpyxl
from openpyxl.styles import Font, Border, Side

@login_required
def order_tree_export(request, order_id):
    if not request.user.is_staff:
        messages.error(request, 'Недостаточно прав.')
        return redirect('order_tree', order_id=order_id)
    
    order = get_object_or_404(Order, pk=order_id)
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f'Заказ {order.order_number}'
    
    headers = ['Обозначение', 'Наименование', 'Тип', 'Кол-во по плану', 'Статус', 'Прогресс', 'Годных', 'Текущая операция', 'Нехватка']
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
        cell.border = Border(bottom=Side(style='thin'))
    
    def add_items(items, level=0, start_row=2):
        row = start_row
        for item in items:
            inst = item.instances.first()
            designation = '  ' * level + item.item.item_number
            ws.cell(row=row, column=1, value=designation)
            ws.cell(row=row, column=2, value=item.item.name)
            ws.cell(row=row, column=3, value=item.item.item_type)
            ws.cell(row=row, column=4, value=item.planned_quantity())
            
            if inst:
                status = inst.assembly_status() if inst.item.item_type == 'Сборочная единица' else inst.current_operation_name()
                ws.cell(row=row, column=5, value=status)
                ws.cell(row=row, column=6, value=f"{inst.completion_percent()}%")
                ws.cell(row=row, column=7, value=inst.good_produced())
                ws.cell(row=row, column=8, value=inst.current_operation_name())
                ws.cell(row=row, column=9, value=inst.shortage())
            else:
                ws.cell(row=row, column=5, value='Нет экземпляра')
            
            row += 1
            children = item.children.all()
            if children:
                row = add_items(children, level + 1, row)
        return row
    
    add_items(order.items.filter(parent__isnull=True))
    
    for col in ws.columns:
        max_length = 0
        column_letter = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = (max_length + 2) * 1.2
        ws.column_dimensions[column_letter].width = adjusted_width
    
    from io import BytesIO
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    response = HttpResponse(output.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Заказ_{order.order_number}_отчёт.xlsx"'
    return response

import openpyxl
from openpyxl.styles import Font, Border, Side, PatternFill
from datetime import datetime, timedelta

@login_required
def warehouse_report(request):
    # Получаем даты из GET-параметров, если не заданы – последние 30 дней
    start_date = request.GET.get('start')
    end_date = request.GET.get('end')
    today = timezone.now().date()
    if not start_date:
        start_date = (today - timedelta(days=30)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = today.strftime('%Y-%m-%d')
    
    # Преобразуем в datetime для фильтрации
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
    
    records = WarehouseRecord.objects.select_related('instance__item', 'employee').filter(
        date__gte=start_dt, date__lt=end_dt
    ).order_by('-date')
    
    # Создаём Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Складской отчёт'
    
    # Заголовок
    ws.merge_cells('A1:I1')
    ws['A1'] = f'Складской отчёт за период: {start_date} – {end_date}'
    ws['A1'].font = Font(bold=True, size=12)
    
    # Шапка таблицы
    headers = ['Дата', 'Тип', 'Деталь', 'Партия', 'Кол-во', 'Кому', 'Основание', 'Сотрудник', 'Примечание']
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='00557A', end_color='00557A', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
    
    # Данные
    for i, rec in enumerate(records, 4):
        ws.cell(row=i, column=1, value=rec.date.strftime('%d.%m.%Y %H:%M')).border = thin_border
        ws.cell(row=i, column=2, value=rec.get_movement_type_display()).border = thin_border
        ws.cell(row=i, column=3, value=f"{rec.instance.item.item_number} – {rec.instance.item.name}").border = thin_border
        ws.cell(row=i, column=4, value=rec.instance.serial).border = thin_border
        ws.cell(row=i, column=5, value=rec.quantity).border = thin_border
        ws.cell(row=i, column=6, value=rec.recipient or '—').border = thin_border
        ws.cell(row=i, column=7, value=rec.basis or '—').border = thin_border
        ws.cell(row=i, column=8, value=rec.employee.last_name if rec.employee else '—').border = thin_border
        ws.cell(row=i, column=9, value=rec.notes or '—').border = thin_border
    
    # Автоширина (без учёта объединённых ячеек)
    for col_idx in range(1, len(headers) + 1):
        max_length = 0
        for row in ws.iter_rows(min_col=col_idx, max_col=col_idx, values_only=True):
            for value in row:
                if value and len(str(value)) > max_length:
                    max_length = len(str(value))
        adjusted_width = (max_length + 2) * 1.2
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = adjusted_width
    
    from io import BytesIO
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    response = HttpResponse(output.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Складской_отчёт_{start_date}_{end_date}.xlsx"'
    return response
