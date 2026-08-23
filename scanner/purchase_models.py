from django.db import models
from django.contrib.auth.models import User

class PurchaseItem(models.Model):
    PURCHASE_STATUS_CHOICES = [
        ("pending", "Ожидает закупки"),
        ("awaiting_payment", "Ожидает оплаты"),
        ("paid", "Оплачен"),
        ("shipped", "Отгружен"),
        ("received_warehouse", "Получено на склад"),
        ("sent_to_inspection", "Передано на входной контроль"),
        ("incoming_inspection", "Прошел входной контроль"),
        ("galvanika", "Гальваника"),
        ("ready_for_issue", "Готов к выдаче"),
        ("issued", "Выдан"),
    ]

    order = models.ForeignKey('scanner.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_items')
    assembly_ref = models.ForeignKey('scanner.OrderItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_refs',
                                     verbose_name='Подсборка (ссылка)')
    item_name = models.CharField(max_length=500, verbose_name='Наименование')
    designation = models.CharField(max_length=300, blank=True, verbose_name='Обозначение')
    assembly_name = models.CharField(max_length=500, blank=True, verbose_name='Подсборка (название)')
    quantity_required = models.PositiveIntegerField(default=0, verbose_name='Требуемое количество')
    quantity_purchased = models.PositiveIntegerField(default=0, verbose_name='Закупленное количество')
    notes = models.TextField(blank=True, verbose_name='Примечание (общее для заказа)')
    purchase_status = models.CharField(max_length=20, choices=PURCHASE_STATUS_CHOICES, default='pending', verbose_name='Статус')
    created_at = models.DateTimeField(auto_now_add=True)

    issued_quantity = models.PositiveIntegerField(default=0, verbose_name='Выдано')

    class Meta:
        verbose_name = 'Покупное изделие'
        verbose_name_plural = 'Покупные изделия'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.item_name} ({self.quantity_purchased} шт.)"


class PurchaseTransaction(models.Model):
    TRANSACTION_TYPES = [
        ("in", "Приход"),
        ("out", "Выдача"),
    ]

    purchase_item = models.ForeignKey(PurchaseItem, on_delete=models.CASCADE, related_name='transactions')
    transaction_type = models.CharField(max_length=3, choices=TRANSACTION_TYPES, verbose_name='Тип')
    quantity = models.PositiveIntegerField(default=0, verbose_name='Количество')
    recipient = models.CharField(max_length=255, blank=True, verbose_name='Получатель')
    basis = models.CharField(max_length=255, blank=True, verbose_name='Основание')
    batch_token = models.CharField(max_length=64, blank=True, db_index=True, verbose_name='Токен группы')
    batch_token = models.CharField(max_length=64, blank=True, db_index=True, verbose_name='Токен группы')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Исполнитель')

    issued_quantity = models.PositiveIntegerField(default=0, verbose_name='Выдано')

    class Meta:
        verbose_name = 'Движение покупного изделия'
        verbose_name_plural = 'Движения покупных изделий'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_transaction_type_display()} — {self.purchase_item.item_name} ({self.quantity} шт.)"
