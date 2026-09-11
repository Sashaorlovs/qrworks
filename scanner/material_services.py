from decimal import Decimal, InvalidOperation
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from scanner.material_models import (
    MaterialRequest,
    MaterialRequestLine,
    MaterialStockLot,
    MaterialTransaction,
    ZERO,
)


def decimal_value(value, default=ZERO):
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"Некорректное число: {value}")


def validate_material_geometry(item):
    if item.profile_type not in dict(item.PROFILE_CHOICES):
        raise ValidationError("Неизвестный тип профиля.")

    def absent(field):
        value = getattr(item, field)
        return value is None or value <= 0

    missing = []
    if item.profile_type == "sheet":
        for field, label in [("thickness_mm", "толщина"), ("width_mm", "ширина"), ("piece_length_mm", "длина листа")]:
            if absent(field):
                missing.append(label)
    elif item.profile_type == "round_pipe":
        if absent("outer_diameter_mm"):
            missing.append("наружный диаметр")
        if absent("wall_thickness_mm"):
            missing.append("толщина стенки")
        if not absent("outer_diameter_mm") and not absent("wall_thickness_mm") and item.outer_diameter_mm <= item.wall_thickness_mm * 2:
            raise ValidationError("У круглой трубы наружный диаметр должен быть больше двойной толщины стенки.")
    elif item.profile_type == "rect_tube":
        for field, label in [("width_mm", "ширина"), ("height_mm", "высота"), ("wall_thickness_mm", "толщина стенки")]:
            if absent(field):
                missing.append(label)
        if not any(absent(field) for field in ["width_mm", "height_mm", "wall_thickness_mm"]) and (
            item.width_mm <= item.wall_thickness_mm * 2 or item.height_mm <= item.wall_thickness_mm * 2
        ):
            raise ValidationError("У профильной трубы ширина и высота должны быть больше двойной толщины стенки.")
    elif item.profile_type == "round_bar" and absent("outer_diameter_mm"):
        missing.append("диаметр")
    elif item.profile_type == "square_bar" and absent("width_mm"):
        missing.append("сторона квадрата")
    elif item.profile_type == "rect_bar" and (absent("width_mm") or (absent("height_mm") and absent("thickness_mm"))):
        missing.append("ширина и высота/толщина")
    elif item.profile_type in {"angle", "channel", "beam", "other"} and absent("kg_per_meter"):
        missing.append("масса 1 м")
    if item.is_linear and not (getattr(item, "total_length_required_mm", ZERO) or getattr(item, "length_initial_mm", ZERO) or item.piece_length_mm):
        missing.append("общая длина или длина единицы")
    if missing:
        raise ValidationError("Не заполнено: " + ", ".join(missing) + ".")


def stock_matches_requirement(lot, requirement):
    if lot.grade_id != requirement.grade_id or lot.profile_type != requirement.profile_type:
        return False
    for field in ["thickness_mm", "width_mm", "height_mm", "outer_diameter_mm", "wall_thickness_mm", "kg_per_meter"]:
        left = getattr(lot, field)
        right = getattr(requirement, field)
        if (left is None) != (right is None) or (left is not None and left != right):
            return False
    if requirement.profile_type == "sheet":
        return lot.piece_length_mm == requirement.piece_length_mm
    if requirement.profile_type in {"angle", "channel", "beam", "other"}:
        return (lot.profile_name or "").strip().casefold() == (requirement.profile_name or "").strip().casefold()
    return True


def matching_lots(requirement, lock=False):
    qs = MaterialStockLot.objects.filter(
        Q(order__isnull=True) | Q(order=requirement.order),
        grade_id=requirement.grade_id,
        profile_type=requirement.profile_type,
    ).select_related("grade", "order")
    if lock:
        qs = qs.select_for_update()
    return [lot for lot in qs.order_by("received_at", "id") if stock_matches_requirement(lot, requirement)]


def available_for_requirement(requirement):
    lots = matching_lots(requirement)
    return {
        "quantity": sum((lot.quantity_remaining for lot in lots), ZERO),
        "length_mm": sum((lot.length_remaining_mm for lot in lots), ZERO),
        "mass_kg": sum((lot.mass_remaining_kg for lot in lots), ZERO),
    }


def issued_for_requirement(requirement):
    return MaterialTransaction.objects.filter(transaction_type="out", request_line__requirement=requirement).aggregate(
        quantity=Sum("quantity"), length=Sum("length_mm"), mass=Sum("mass_kg")
    )


def create_material_request(order, requirements, user, purpose=""):
    with transaction.atomic():
        date_part = timezone.localdate().strftime("%Y%m%d")
        sequence = MaterialRequest.objects.filter(created_at__date=timezone.localdate()).count() + 1
        number = f"МЗ-{date_part}-{sequence:03d}"
        while MaterialRequest.objects.filter(number=number).exists():
            sequence += 1
            number = f"МЗ-{date_part}-{sequence:03d}"
        document = MaterialRequest.objects.create(number=number, order=order, purpose=purpose, requested_by=user)
        created = 0
        for requirement in requirements:
            issued = issued_for_requirement(requirement)
            issued_quantity = issued["quantity"] or ZERO
            issued_length = issued["length"] or ZERO
            open_lines = MaterialRequestLine.objects.filter(
                requirement=requirement, request__status__in=["open", "partial"]
            ).aggregate(quantity=Sum("quantity_requested"), length=Sum("length_requested_mm"))
            open_quantity = open_lines["quantity"] or ZERO
            open_length = open_lines["length"] or ZERO
            if requirement.is_linear:
                length = max(ZERO, requirement.total_length_required_mm - issued_length - open_length)
                if length <= 0:
                    continue
                quantity = max(ZERO, requirement.quantity_required - issued_quantity - open_quantity)
            else:
                quantity = max(ZERO, requirement.quantity_required - issued_quantity - open_quantity)
                if quantity <= 0:
                    continue
                length = ZERO
            MaterialRequestLine.objects.create(
                request=document,
                requirement=requirement,
                quantity_requested=quantity,
                length_requested_mm=length,
                mass_requested_kg=requirement.calculate_mass(quantity, length),
            )
            created += 1
        if not created:
            raise ValidationError("По выбранным позициям нет незаявленной потребности.")
        return document


def issue_request_lines(request_document, line_values, recipient, basis, user):
    batch_token = uuid.uuid4().hex
    created_transactions = []
    with transaction.atomic():
        document = MaterialRequest.objects.select_for_update().get(pk=request_document.pk)
        if document.status not in {"open", "partial"}:
            raise ValidationError("Эта заявка уже закрыта или отменена.")
        for line_id, values in line_values.items():
            line = MaterialRequestLine.objects.select_for_update().select_related("requirement__grade", "request__order").get(pk=line_id, request=document)
            requirement = line.requirement
            quantity = decimal_value(values.get("quantity"))
            length = decimal_value(values.get("length_mm"))
            if requirement.is_linear:
                if length <= 0:
                    continue
                if length > line.length_remaining_mm:
                    raise ValidationError(f"{requirement.item_name}: длина превышает остаток заявки.")
            else:
                if quantity <= 0:
                    continue
                if quantity > line.quantity_remaining:
                    raise ValidationError(f"{requirement.item_name}: количество превышает остаток заявки.")
            lots = matching_lots(requirement, lock=True)
            if not lots:
                raise ValidationError(f"{requirement.item_name}: подходящего материала на складе нет.")
            remaining_quantity = quantity
            remaining_length = length
            for lot in lots:
                if requirement.is_linear:
                    take_length = min(remaining_length, lot.length_remaining_mm)
                    if take_length <= 0:
                        continue
                    take_quantity = ZERO
                    take_mass = lot.calculate_mass(ZERO, take_length)
                    lot.length_remaining_mm -= take_length
                    lot.mass_remaining_kg = lot.calculate_mass(ZERO, lot.length_remaining_mm)
                    remaining_length -= take_length
                else:
                    take_quantity = min(remaining_quantity, lot.quantity_remaining)
                    if take_quantity <= 0:
                        continue
                    take_length = ZERO
                    take_mass = lot.calculate_mass(take_quantity, ZERO)
                    lot.quantity_remaining -= take_quantity
                    lot.mass_remaining_kg = lot.calculate_mass(lot.quantity_remaining, ZERO)
                    remaining_quantity -= take_quantity
                lot.save(update_fields=["quantity_remaining", "length_remaining_mm", "mass_remaining_kg"])
                created_transactions.append(MaterialTransaction.objects.create(
                    stock_lot=lot,
                    request_line=line,
                    order=document.order,
                    transaction_type="out",
                    quantity=take_quantity,
                    length_mm=take_length,
                    mass_kg=take_mass,
                    recipient=recipient,
                    recipient_name=str(recipient),
                    basis=basis or document.purpose or document.number,
                    batch_token=batch_token,
                    created_by=user,
                ))
            if (requirement.is_linear and remaining_length > 0) or (not requirement.is_linear and remaining_quantity > 0):
                raise ValidationError(f"{requirement.item_name}: недостаточный остаток на складе.")
            line.quantity_issued += quantity if not requirement.is_linear else ZERO
            line.length_issued_mm += length if requirement.is_linear else ZERO
            line.mass_issued_kg = MaterialTransaction.objects.filter(request_line=line, transaction_type="out").aggregate(total=Sum("mass_kg"))["total"] or ZERO
            line.save(update_fields=["quantity_issued", "length_issued_mm", "mass_issued_kg"])
        if not created_transactions:
            raise ValidationError("Не указано количество или длина для выдачи.")
        open_exists = any(
            (line.requirement.is_linear and line.length_remaining_mm > 0)
            or (not line.requirement.is_linear and line.quantity_remaining > 0)
            for line in document.lines.select_related("requirement__grade")
        )
        document.status = "partial" if open_exists else "issued"
        document.save(update_fields=["status"])
    return batch_token, created_transactions
