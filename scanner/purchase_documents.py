import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Max, Sum
from django.utils import timezone

from scanner.purchase_allocation import (
    PurchaseAllocationError,
    allocate_purchase_items,
    allocation_state,
)
from scanner.purchase_models import (
    PurchaseItem,
    PurchaseInvoiceCounter,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseTransaction,
)


def general_surplus_state(item):
    """Return stock that is not reserved for any assembly in the order."""
    same_items = PurchaseItem.objects.filter(
        order_id=item.order_id,
        item_name__iexact=item.item_name.strip(),
    )
    purchased = same_items.aggregate(value=Max('quantity_purchased'))['value'] or 0
    required = same_items.aggregate(value=Sum('quantity_required'))['value'] or 0
    issued_total = PurchaseTransaction.objects.filter(
        purchase_item__in=same_items, transaction_type='out'
    ).aggregate(value=Sum('quantity'))['value'] or 0
    issued_to_assemblies = PurchaseTransaction.objects.filter(
        purchase_item__in=same_items,
        transaction_type='out',
        is_general_use=False,
    ).aggregate(value=Sum('quantity'))['value'] or 0
    stock_available = max(purchased - issued_total, 0)
    reserved_for_assemblies = max(required - issued_to_assemblies, 0)
    return {
        'purchased': purchased,
        'stock_available': stock_available,
        'reserved': reserved_for_assemblies,
        'general_available': max(stock_available - reserved_for_assemblies, 0),
    }


def assign_invoice_number(movements):
    """Attach the next independent, year-based invoice number to a batch."""
    movements = list(movements)
    if not movements:
        raise ValidationError('Накладная не содержит позиций.')
    existing = next((row.document_number for row in movements if row.document_number), '')
    if existing:
        return existing
    year = timezone.localdate().year
    counter, _ = PurchaseInvoiceCounter.objects.select_for_update().get_or_create(
        year=year,
        defaults={'last_number': 0},
    )
    counter.last_number += 1
    counter.save(update_fields=['last_number'])
    number = f'НК-{year}-{counter.last_number:06d}'
    ids = [row.id for row in movements]
    PurchaseTransaction.objects.filter(id__in=ids).update(document_number=number)
    for row in movements:
        row.document_number = number
    return number


@transaction.atomic
def create_purchase_request(order, item_ids, user, purpose=''):
    items = list(
        PurchaseItem.objects.select_for_update()
        .filter(order=order, id__in=item_ids)
        .order_by('item_name', 'assembly_name', 'id')
    )
    if not items:
        raise ValidationError('Выберите хотя бы одну позицию этого проекта.')

    today = timezone.localdate()
    sequence = PurchaseRequest.objects.filter(created_at__date=today).count() + 1
    number = f"КЗ-{today:%Y%m%d}-{sequence:03d}"
    while PurchaseRequest.objects.filter(number=number).exists():
        sequence += 1
        number = f"КЗ-{today:%Y%m%d}-{sequence:03d}"

    if not purpose.strip():
        assemblies = list(dict.fromkeys(
            item.assembly_name.strip()
            for item in items
            if item.assembly_name and item.assembly_name.strip()
        ))
        purpose = '; '.join(assemblies) or (order.full_name or order.order_number)

    document = PurchaseRequest.objects.create(
        number=number,
        order=order,
        purpose=purpose.strip(),
        requested_by=user,
    )
    created = 0
    for item in items:
        issued = PurchaseTransaction.objects.filter(
            purchase_item=item, transaction_type='out'
        ).aggregate(total=Sum('quantity'))['total'] or 0
        open_lines = PurchaseRequestLine.objects.filter(
            purchase_item=item,
            request__status__in=['open', 'partial'],
        )
        already_requested = sum(line.quantity_remaining for line in open_lines)
        quantity = max((item.quantity_required or 0) - issued - already_requested, 0)
        if quantity <= 0:
            continue
        PurchaseRequestLine.objects.create(
            request=document,
            purchase_item=item,
            quantity_requested=quantity,
        )
        created += 1

    if not created:
        raise ValidationError('По выбранным позициям нет незаявленной потребности.')
    return document


@transaction.atomic
def issue_purchase_request(document, quantities, recipient, basis, user, issuer=None):
    locked_document = PurchaseRequest.objects.select_for_update().get(pk=document.pk)
    if locked_document.status not in {'open', 'partial'}:
        raise ValidationError('Эта заявка уже закрыта или отменена.')

    selected_lines = list(
        PurchaseRequestLine.objects.select_for_update()
        .select_related('purchase_item__order')
        .filter(request=locked_document, id__in=quantities)
        .order_by('id')
    )
    if not selected_lines:
        raise ValidationError('Выберите хотя бы одну позицию для выдачи.')

    allocation_lines = []
    line_by_item = {}
    for line in selected_lines:
        try:
            quantity = int(quantities.get(line.id, 0))
        except (TypeError, ValueError):
            raise ValidationError('Количество должно быть целым числом.')
        if quantity <= 0:
            raise ValidationError('Количество к выдаче должно быть больше нуля.')
        if quantity > line.quantity_remaining:
            raise ValidationError(
                f'{line.purchase_item.item_name}: количество превышает остаток заявки.'
            )
        if line.purchase_item.purchase_status != 'ready_for_issue':
            raise ValidationError(
                f'{line.purchase_item.item_name}: позиция ещё не имеет статуса «Готов к выдаче».'
            )
        state = allocation_state(line.purchase_item)
        if quantity > state.allocatable:
            raise ValidationError(
                f'{line.purchase_item.item_name}: сейчас можно выдать не более '
                f'{state.allocatable} шт.'
            )
        allocation_lines.append({
            'purchase_item_id': line.purchase_item_id,
            'quantity': quantity,
        })
        line_by_item[line.purchase_item_id] = line

    batch_token = uuid.uuid4().hex
    try:
        movements = allocate_purchase_items(
            lines=allocation_lines,
            recipient=recipient,
            basis=basis.strip() or locked_document.purpose or locked_document.number,
            batch_token=batch_token,
            created_by=user,
        )
    except PurchaseAllocationError as exc:
        raise ValidationError(str(exc))

    invoice_number = assign_invoice_number(movements)
    for movement in movements:
        line = line_by_item[movement.purchase_item_id]
        movement.request_line = line
        movement.issuer_name = str(issuer).strip() if issuer else ''
        movement.save(update_fields=['request_line', 'issuer_name'])
        line.quantity_issued += movement.quantity
        line.save(update_fields=['quantity_issued'])

    locked_document.status = (
        'partial'
        if locked_document.lines.filter(quantity_issued__lt=F('quantity_requested')).exists()
        else 'issued'
    )
    locked_document.save(update_fields=['status'])
    return batch_token, invoice_number, movements


@transaction.atomic
def issue_general_surplus(item_id, quantity, recipient, basis, user, issuer=None):
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValidationError('Количество должно быть целым числом.')
    if quantity <= 0:
        raise ValidationError('Количество к выдаче должно быть больше нуля.')

    item = PurchaseItem.objects.select_for_update().select_related('order').get(pk=item_id)
    if item.purchase_status != 'ready_for_issue':
        raise ValidationError(
            f'{item.item_name}: выдача разрешена только в статусе «Готов к выдаче».'
        )
    list(
        PurchaseItem.objects.select_for_update().filter(
            order_id=item.order_id,
            item_name__iexact=item.item_name.strip(),
        ).order_by('id')
    )
    state = general_surplus_state(item)
    if quantity > state['general_available']:
        raise ValidationError(
            f'{item.item_name}: свободно только {state["general_available"]} шт. '
            f'Остальное зарезервировано под сборки.'
        )

    batch_token = uuid.uuid4().hex
    movement = PurchaseTransaction.objects.create(
        purchase_item=item,
        transaction_type='out',
        quantity=quantity,
        recipient=str(recipient),
        basis=basis.strip() or 'Общепроизводственные нужды',
        batch_token=batch_token,
        created_by=user,
        is_general_use=True,
        issuer_name=str(issuer).strip() if issuer else '',
    )
    number = assign_invoice_number([movement])
    return batch_token, number, movement
