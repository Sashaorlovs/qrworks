from django.db.models import Q
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
    orders = Order.objects.all().order_by('order_number')
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
    root_items = order.items.filter(parent__isnull=True).prefetch_related(
        'instances__route_card__operations__operation_type'
    )
    # ID экземпляров, у которых есть приоритетные операции
    priority_ids = set()
    for item in order.items.all():
        for inst in item.instances.all():
            if hasattr(inst, 'route_card') and inst.route_card:
                if inst.route_card.operations.filter(
                    operation_type__name__in=['Гальваника', 'Расточная']
                ).exists():
                    priority_ids.add(inst.id)
    return render(request, 'scanner/order_tree.html', {
        'order': order,
        'root_items': root_items,
        'priority_ids': priority_ids,
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
            new_good = int(request.POST.get('good_qty', 0) or 0)
            new_bad = int(request.POST.get('bad_qty', 0) or 0)

            # накапливаем годные и брак
            op.good_qty = (op.good_qty or 0) + new_good
            op.bad_qty = (op.bad_qty or 0) + new_bad
            notes = request.POST.get('notes', '')
            if notes:
                op.notes = notes

            # Проверяем, выполнена ли норма
            planned = instance.planned_quantity()
            if op.good_qty >= planned:
                op.status = 'completed'
                op.completed_at = timezone.now()
                op.save()

                # складской приход только при окончательном завершении
                if op.operation_type.name in ('Прием на меж.операционный склад', 'Прием на склад'):
                    movement = 'in_main' if op.operation_type.name == 'Прием на склад' else 'in_intermediate'
                    WarehouseRecord.objects.create(
                        instance=instance,
                        movement_type=movement,
                        quantity=op.good_qty,
                        employee=request.user.employee if hasattr(request.user, 'employee') else None,
                        basis=f'Завершение операции «{op.operation_type.name}»',
                        notes=op.notes or ''
                    )
                # активируем следующую операцию
                next_op = route_card.operations.filter(order=op.order + 1).first()
                if next_op and next_op.status == 'pending':
                    pass
            else:
                # операция остаётся в работе
                op.save()

        return redirect('instance_detail', item_number=item_number, serial=serial)

    status_info = route_card.get_status()
    operations = route_card.operations.select_related('operation_type', 'worker').order_by('order')
    planned = instance.planned_quantity()
    for op in operations:
        op.remaining = planned - (op.good_qty or 0)
        op.requires_location = op.operation_type.name in ('Прием на склад', 'Прием на меж.операционный склад')
    # помечаем складские операции как требующие место
    for op in operations:
        op.requires_location = op.operation_type.name in ('Прием на склад', 'Прием на меж.операционный склад')

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
            order=old.order,
            order_item=old.order_item
        )
        RouteCard.objects.create(instance=new_inst)
    return redirect('instance_detail', item_number=old.item.item_number, serial=old.serial)

@login_required
def route_card_print(request, route_card_id):
    from openpyxl.styles import PatternFill, Alignment as XlAlignment, Font as XlFont
    from openpyxl.utils import get_column_letter
    from openpyxl.cell.cell import MergedCell
    from datetime import datetime

    def safe_write(ws, row, col, value, alignment=None):
        cell = ws.cell(row=row, column=col)
        if not isinstance(cell, MergedCell):
            cell.value = value
            if alignment:
                cell.alignment = alignment
            return cell
        return None

    def apply_border_to_range(ws, start_row, start_col, end_row, end_col, border):
        """Применить границу ко всем ячейкам диапазона, включая объединённые"""
        for r in range(start_row, end_row + 1):
            for c in range(start_col, end_col + 1):
                cell = ws.cell(row=r, column=c)
                if not isinstance(cell, MergedCell):
                    cell.border = border

    rc = get_object_or_404(RouteCard, pk=route_card_id)
    ops = rc.operations.select_related('operation_type', 'worker').order_by('order')
    instance = rc.instance
    template_path = '/opt/qr_simple/templates/route_template.xlsx'
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active

    # Сбрасываем все объединения
    merged_ranges = [str(m) for m in ws.merged_cells.ranges]
    for rng in merged_ranges:
        ws.unmerge_cells(rng)

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
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    header_font = XlFont(bold=True, size=10)
    data_font = XlFont(size=10)
    center_wrap = XlAlignment(horizontal='center', vertical='center', wrap_text=True)
    left_wrap   = XlAlignment(horizontal='left', vertical='center', wrap_text=True)

    # Заголовок A1:D1 (без границ)
    ws.merge_cells('A1:D1')
    safe_write(ws, 1, 1, 'Маршрутно-операционный лист', center_wrap)
    c = ws['A1']
    if not isinstance(c, MergedCell):
        c.font = XlFont(bold=True, size=14)

    # Изделие (строка 2)
    safe_write(ws, 2, 1, 'Изделие:', left_wrap)
    ws.merge_cells('B2:D2')
    if root_item:
        safe_write(ws, 2, 2, f"{root_item.item.item_number} – {root_item.item.name}", center_wrap)
    else:
        safe_write(ws, 2, 2, instance.item.name, center_wrap)
    ws.row_dimensions[2].height = 35
    apply_border_to_range(ws, 2, 1, 2, 4, thin_border)   # A2:D2

    # Деталь (строка 3)
    safe_write(ws, 3, 1, 'Деталь:', left_wrap)
    ws.merge_cells('B3:D3')
    detail_str = f"{instance.item.item_number} – {instance.item.name}"
    if parent_item and parent_item != root_item:
        detail_str += f" (входит в {parent_item.item.item_number} – {parent_item.item.name})"
    safe_write(ws, 3, 2, detail_str, center_wrap)
    ws.row_dimensions[3].height = 35
    apply_border_to_range(ws, 3, 1, 3, 4, thin_border)   # A3:D3

    # Дата запуска (строка 4)
    safe_write(ws, 4, 1, 'Дата запуска:', left_wrap)
    ws.merge_cells('B4:D4')
    safe_write(ws, 4, 2, instance.created_at.strftime('%d.%m.%Y') if instance.created_at else '', center_wrap)
    apply_border_to_range(ws, 4, 1, 4, 4, thin_border)

    # Количество (строка 5)
    safe_write(ws, 5, 1, 'Количество:', left_wrap)
    ws.merge_cells('B5:D5')
    safe_write(ws, 5, 2, instance.planned_quantity(), center_wrap)
    apply_border_to_range(ws, 5, 1, 5, 4, thin_border)

    # Материал (строка 6)
    safe_write(ws, 6, 1, 'Материал:', left_wrap)
    ws.merge_cells('B6:D6')
    if instance.item.material:
        safe_write(ws, 6, 2, instance.item.material.name, center_wrap)
    else:
        safe_write(ws, 6, 2, 'не указан', center_wrap)
    apply_border_to_range(ws, 6, 1, 6, 4, thin_border)

    # Профиль (сортамент) – если есть
    has_profile = bool(instance.item.profile)
    offset = 0
    if has_profile:
        ws.insert_rows(7)
        # Записываем значение для A7
        safe_write(ws, 7, 1, 'Сортамент:', left_wrap)
        apply_border_to_range(ws, 7, 1, 7, 1, thin_border)
        # Для B7, C7, D7 сначала ставим границы (до объединения)
        for col in [2, 3, 4]:
            apply_border_to_range(ws, 7, col, 7, col, thin_border)
        # Записываем значение и объединяем
        safe_write(ws, 7, 2, instance.item.profile, center_wrap)
        ws.merge_cells('B7:D7')
        offset = 1

    # Размер заготовки
    safe_write(ws, 7 + offset, 1, 'Размер заготовки:', left_wrap)
    ws.merge_cells(f'B{7+offset}:D{7+offset}')
    safe_write(ws, 7+offset, 2, instance.item.blank_size or 'не указан', center_wrap)
    apply_border_to_range(ws, 7+offset, 1, 7+offset, 4, thin_border)

    # Кол-во заготовок
    safe_write(ws, 8 + offset, 1, 'Кол-во заготовок:', left_wrap)
    ws.merge_cells(f'B{8+offset}:D{8+offset}')
    safe_write(ws, 8+offset, 2, instance.item.blanks_per_item if instance.item.blanks_per_item else '', center_wrap)
    apply_border_to_range(ws, 8+offset, 1, 8+offset, 4, thin_border)

    # Пустая строка-разделитель (без границ)
    separator_row = 9 + offset
    for c in range(1, 8):
        cell = ws.cell(row=separator_row, column=c)
        if not isinstance(cell, MergedCell):
            cell.value = ''
            cell.border = Border()

    # Заголовки таблицы
    header_row = 10 + offset
    for col in range(1, 8):
        cell = ws.cell(row=header_row, column=col)
        if not isinstance(cell, MergedCell):
            cell.font = header_font
            cell.border = thin_border
            cell.alignment = center_wrap

    # Данные операций
    for i, op in enumerate(ops):
        row = 11 + offset + i
        safe_write(ws, row, 1, op.operation_type.name, left_wrap)
        safe_write(ws, row, 2, float(op.planned_hours), center_wrap)
        safe_write(ws, row, 3, '', center_wrap)
        safe_write(ws, row, 4, op.worker.get_full_name() if op.worker else '', left_wrap)
        safe_write(ws, row, 5, op.get_status_display(), center_wrap)
        safe_write(ws, row, 6, f"{op.good_qty}/{op.bad_qty}", center_wrap)
        safe_write(ws, row, 7, op.notes or '', left_wrap)
        apply_border_to_range(ws, row, 1, row, 7, thin_border)

    # Ширина столбцов
    widths = {'A': 22, 'B': 12, 'C': 14, 'D': 16, 'E': 12, 'F': 12, 'G': 16}
    for col_letter, w in widths.items():
        ws.column_dimensions[col_letter].width = w

    for row in range(header_row, 11 + offset + len(ops)):
        ws.row_dimensions[row].height = None

    # Подпись
    signature_row = 11 + offset + len(ops) + 1
    who = ''
    if hasattr(request.user, 'employee') and request.user.employee:
        emp = request.user.employee
        who = f"{emp.last_name} {emp.first_name} {emp.middle_name or ''}".replace('  ', ' ').strip()
    if not who:
        who = request.user.get_full_name() or request.user.username
    ws.merge_cells(f'A{signature_row}:G{signature_row}')
    c = ws[f'A{signature_row}']
    if not isinstance(c, MergedCell):
        c.value = f'Документ сформировал: {who}'
        c.font = XlFont(italic=True, size=10)
        c.alignment = XlAlignment(horizontal='left', vertical='center')

    # Дата печати
    print_date_row = signature_row + 1
    ws.merge_cells(f'A{print_date_row}:G{print_date_row}')
    c = ws[f'A{print_date_row}']
    if not isinstance(c, MergedCell):
        c.value = f'Дата печати: {datetime.now().strftime("%d.%m.%Y %H:%M")}'
        c.font = XlFont(italic=True, size=10)
        c.alignment = XlAlignment(horizontal='left', vertical='center')

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
                inst = ItemInstance.objects.create(
                    item=item,
                    serial=serial,
                    quantity=oi.quantity,
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
def warehouse_dashboard(request):
    instances = ItemInstance.objects.select_related('item').all()
    main_data = []
    intermediate_data = []
    employee_list = Employee.objects.filter(is_active=True)
    for inst in instances:
        order_number = inst.order.order_number if inst.order else ''
        receipt_date_main = ''
        receipt_date_inter = ''
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
        # Место хранения (из последней записи прихода с непустым примечанием)
        location_main = ''
        for rec in inst.warehouse_records.filter(movement_type='in_main').order_by('-date'):
            if rec.notes:
                location_main = rec.notes
                break
        # Для меж.операционного аналогично
        location_inter = ''
        for rec in inst.warehouse_records.filter(movement_type='in_intermediate').order_by('-date'):
            if rec.notes:
                location_inter = rec.notes
                break

        if balance_main > 0:
            main_data.append({
                'instance': inst,
                'balance': balance_main,
                'assembly': root_name,
                'order_number': order_number,
                'location': location_main,
                'receipt_date': receipt_date_main,
                'receipt_date': receipt_date_main,
            })
        if balance_inter > 0:
            intermediate_data.append({
                'instance': inst,
                'balance': balance_inter,
                'assembly': root_name,
                'order_number': order_number,
                'location': location_inter,
                'receipt_date': receipt_date_inter,
                'receipt_date': receipt_date_inter,
            })

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
        warehouse_type = request.POST.get('warehouse_type', 'main')
        if warehouse_type == 'intermediate':
            in_filter = ['in_intermediate', 'in']
            out_filter = ['out_intermediate', 'out']
            movement_out = 'out_intermediate'
            if not request.POST.get('notes', '').strip():
                messages.error(request, 'Необходимо указать примечание (куда направлена деталь).')
                return redirect('warehouse')
        else:
            in_filter = ['in_main', 'in']
            out_filter = ['out_main', 'out']
            movement_out = 'out_main'

        total_in = inst.warehouse_records.filter(
            movement_type__in=in_filter
        ).aggregate(s=models.Sum('quantity'))['s'] or 0
        total_out = inst.warehouse_records.filter(
            movement_type__in=out_filter
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
                instance=inst, movement_type=movement_out, quantity=qty,
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
    from django.db.models import Sum, Count, Q, Q, Q, Q
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
        )[:20]
        results['orders'] = Order.objects.filter(
            Q(order_number__icontains=query) | Q(full_name__icontains=query)
        )[:10]
        results['instances'] = ItemInstance.objects.filter(
            Q(serial__icontains=query)
        ).select_related('item')[:20]
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
    if not request.user.is_staff:
        messages.error(request, 'Недостаточно прав.')
        return redirect('home')
    
    from datetime import date
    orders = Order.objects.all().order_by('order_number').order_by('-created_at')
    today = date.today()
    
    orders_data = []
    for order in orders:
        days_left = None
        if order.due_date:
            delta = order.due_date - today
            days_left = delta.days
        
        # Считаем прогресс выполнения заказа (средний процент по всем операциям)
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
    
    return render(request, 'scanner/orders_control.html', {
        'orders_data': orders_data,
        'today': today,
    })


