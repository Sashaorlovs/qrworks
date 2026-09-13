"""Safe allocation of purchased items to an exact order assembly.

The import format repeats the order-level purchased quantity on every
PurchaseItem row for the same item name.  Therefore stock is the maximum
quantity_purchased inside an order/name group, while demand and issued
quantities are tracked on each exact PurchaseItem (assembly requirement).
"""

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Max, Sum

from scanner.purchase_models import PurchaseItem, PurchaseTransaction


@dataclass(frozen=True)
class AllocationState:
    purchased_for_order: int
    issued_for_order: int
    stock_available: int
    required_for_assembly: int
    issued_for_assembly: int
    demand_available: int
    allocatable: int


class PurchaseAllocationError(ValueError):
    pass


def allocation_state(item: PurchaseItem) -> AllocationState:
    """Return stock and demand limits for one exact assembly requirement."""
    same_item_in_order = PurchaseItem.objects.filter(
        order_id=item.order_id,
        normalized_name=item.normalized_name,
    )
    purchased = same_item_in_order.aggregate(
        value=Max("quantity_purchased")
    )["value"] or 0
    issued_for_order = PurchaseTransaction.objects.filter(
        purchase_item__in=same_item_in_order,
        transaction_type="out",
    ).aggregate(value=Sum("quantity"))["value"] or 0
    issued_for_assembly = PurchaseTransaction.objects.filter(
        purchase_item=item,
        transaction_type="out",
        is_general_use=False,
    ).aggregate(value=Sum("quantity"))["value"] or 0

    required = item.quantity_required or 0
    stock_available = max(purchased - issued_for_order, 0)
    demand_available = max(required - issued_for_assembly, 0)
    return AllocationState(
        purchased_for_order=purchased,
        issued_for_order=issued_for_order,
        stock_available=stock_available,
        required_for_assembly=required,
        issued_for_assembly=issued_for_assembly,
        demand_available=demand_available,
        allocatable=min(stock_available, demand_available),
    )


def _sync_issue_status(item: PurchaseItem) -> None:
    state = allocation_state(item)
    new_status = "issued" if state.demand_available == 0 else "ready_for_issue"
    if item.purchase_status != new_status:
        PurchaseItem.objects.filter(pk=item.pk).update(purchase_status=new_status)
        item.purchase_status = new_status


@transaction.atomic
def allocate_purchase_items(*, lines, recipient, basis, batch_token, created_by):
    """Allocate requested quantities without crossing stock or assembly demand.

    ``lines`` is an iterable of dictionaries with ``purchase_item_id`` and
    ``quantity``.  Repeating the same id is supported and is consolidated.
    All affected order/name groups are locked before validation.
    """
    requested = {}
    for line in lines:
        try:
            item_id = int(line.get("purchase_item_id", line.get("id")))
            quantity = int(line.get("quantity", 0))
        except (TypeError, ValueError):
            raise PurchaseAllocationError("Некорректная позиция или количество")
        if quantity <= 0:
            raise PurchaseAllocationError("Количество к выдаче должно быть больше нуля")
        requested[item_id] = requested.get(item_id, 0) + quantity

    if not requested:
        raise PurchaseAllocationError("Не выбраны позиции для выдачи")

    targets = {
        item.pk: item
        for item in PurchaseItem.objects.select_for_update()
        .select_related("order")
        .filter(pk__in=sorted(requested))
    }
    missing = sorted(set(requested) - set(targets))
    if missing:
        raise PurchaseAllocationError("Одна из выбранных позиций больше не существует")

    # Lock every row sharing stock with a selected target.  A transaction for
    # another assembly of the same order/item must wait for this allocation.
    stock_groups = sorted(
        {(item.order_id, item.normalized_name) for item in targets.values()},
        key=lambda value: ((value[0] or 0), value[1]),
    )
    for order_id, normalized_name in stock_groups:
        candidates = PurchaseItem.objects.select_for_update().filter(order_id=order_id)
        list(candidates.filter(normalized_name=normalized_name).order_by("pk"))

    created = []
    for item_id in sorted(requested):
        item = targets[item_id]
        quantity = requested[item_id]
        state = allocation_state(item)
        if quantity > state.allocatable:
            order_name = item.order.full_name if item.order else "Без заказа"
            assembly = item.assembly_name or "Без подсборки"
            raise PurchaseAllocationError(
                f"{item.item_name}: для «{order_name} / {assembly}» можно выдать "
                f"не более {state.allocatable} шт. "
                f"(не закрыто по узлу: {state.demand_available}, "
                f"доступно по заказу: {state.stock_available})"
            )
        movement = PurchaseTransaction.objects.create(
            purchase_item=item,
            transaction_type="out",
            quantity=quantity,
            recipient=str(recipient),
            basis=basis,
            batch_token=batch_token,
            created_by=created_by,
        )
        _sync_issue_status(item)
        created.append(movement)

    return created
