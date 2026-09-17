from django.db import models
from django.contrib.auth.models import User
from scanner.purchase_normalization import normalize_purchase_name


class PurchasePreparation(models.Model):
    name = models.CharField(max_length=255, unique=True, verbose_name='Название предварительной ведомости')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Создал')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создана')
    assigned_order = models.ForeignKey(
        'scanner.Order', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='purchase_preparations', verbose_name='Привязанный заказ'
    )
    assigned_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата привязки')

    class Meta:
        verbose_name = 'Предварительная ведомость крепежа'
        verbose_name_plural = 'Предварительные ведомости крепежа'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.name

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
    preparation = models.ForeignKey(
        PurchasePreparation, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='items', verbose_name='Предварительная ведомость'
    )
    assembly_ref = models.ForeignKey('scanner.OrderItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_refs',
                                     verbose_name='Подсборка (ссылка)')
    item_name = models.CharField(max_length=500, verbose_name='Наименование')
    normalized_name = models.CharField(max_length=500, blank=True, db_index=True, verbose_name='Нормализованное наименование')
    designation = models.CharField(max_length=300, blank=True, verbose_name='Обозначение')
    assembly_name = models.CharField(max_length=500, blank=True, verbose_name='Подсборка (название)')
    quantity_required = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name='Требуемое количество')
    quantity_purchased = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name='Закупленное количество')
    notes = models.TextField(blank=True, verbose_name='Примечание (общее для заказа)')
    purchase_status = models.CharField(max_length=20, choices=PURCHASE_STATUS_CHOICES, default='pending', verbose_name='Статус')
    created_at = models.DateTimeField(auto_now_add=True)

    issued_quantity = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name='Выдано')

    class Meta:
        verbose_name = 'Покупное изделие'
        verbose_name_plural = 'Покупные изделия'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.item_name} ({self.quantity_purchased} шт.)"

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_purchase_name(self.item_name)
        super().save(*args, **kwargs)


class PurchaseTransaction(models.Model):
    TRANSACTION_TYPES = [
        ("in", "Приход"),
        ("out", "Выдача"),
    ]

    purchase_item = models.ForeignKey(PurchaseItem, on_delete=models.CASCADE, related_name='transactions')
    transaction_type = models.CharField(max_length=3, choices=TRANSACTION_TYPES, verbose_name='Тип')
    quantity = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name='Количество')
    recipient = models.CharField(max_length=255, blank=True, verbose_name='Получатель')
    basis = models.CharField(max_length=255, blank=True, verbose_name='Основание')
    batch_token = models.CharField(max_length=64, blank=True, db_index=True, verbose_name='Токен группы')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Исполнитель')
    document_number = models.CharField(max_length=32, blank=True, db_index=True, verbose_name='Номер накладной')
    request_line = models.ForeignKey(
        'PurchaseRequestLine', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transactions', verbose_name='Строка заявки'
    )
    is_general_use = models.BooleanField(default=False, db_index=True, verbose_name='Общепроизводственные нужды')
    issuer_name = models.CharField(max_length=255, blank=True, verbose_name='Кто выдал (ФИО)')

    issued_quantity = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name='Выдано')

    class Meta:
        verbose_name = 'Движение покупного изделия'
        verbose_name_plural = 'Движения покупных изделий'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_transaction_type_display()} — {self.purchase_item.item_name} ({self.quantity} шт.)"


class PurchaseRequest(models.Model):
    STATUS_CHOICES = [
        ('open', 'Ожидает выдачи'),
        ('partial', 'Выдана частично'),
        ('issued', 'Выдана полностью'),
        ('cancelled', 'Отменена'),
    ]

    number = models.CharField(max_length=32, unique=True, verbose_name='Номер заявки')
    order = models.ForeignKey(
        'scanner.Order', on_delete=models.CASCADE, related_name='purchase_requests',
        verbose_name='Проект / заказ'
    )
    purpose = models.CharField(max_length=255, blank=True, verbose_name='Основание / назначение')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='open', db_index=True, verbose_name='Статус')
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Сформировал')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата')
    cancellation_reason = models.CharField(max_length=1000, blank=True, verbose_name='Причина отмены')
    cancelled_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cancelled_purchase_requests', verbose_name='Отменил'
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата отмены')

    class Meta:
        verbose_name = 'Заявка на стандартные изделия'
        verbose_name_plural = 'Заявки на стандартные изделия'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.number


class PurchaseRequestLine(models.Model):
    request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name='lines')
    purchase_item = models.ForeignKey(
        PurchaseItem, on_delete=models.PROTECT, related_name='request_lines',
        verbose_name='Позиция спецификации'
    )
    quantity_requested = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name='Запрошено')
    quantity_issued = models.DecimalField(max_digits=16, decimal_places=3, default=0, verbose_name='Выдано по заявке')

    class Meta:
        verbose_name = 'Строка заявки на стандартные изделия'
        verbose_name_plural = 'Строки заявок на стандартные изделия'
        ordering = ['purchase_item__item_name', 'id']

    @property
    def quantity_remaining(self):
        return max((self.quantity_requested or 0) - (self.quantity_issued or 0), 0)

    def __str__(self):
        return f'{self.request.number}: {self.purchase_item.item_name}'


class PurchaseInvoiceCounter(models.Model):
    year = models.PositiveIntegerField(unique=True, verbose_name='Год')
    last_number = models.PositiveIntegerField(default=0, verbose_name='Последний номер')

    class Meta:
        verbose_name = 'Счётчик накладных стандартных изделий'
        verbose_name_plural = 'Счётчики накладных стандартных изделий'

    def __str__(self):
        return f'{self.year}: {self.last_number}'
