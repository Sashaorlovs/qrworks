from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class Material(models.Model):
    name = models.CharField(max_length=200, unique=True, verbose_name='Марка')
    code = models.CharField(max_length=50, blank=True, unique=True, verbose_name='Код')
    profile = models.CharField(max_length=200, blank=True, verbose_name='Профиль/сортамент')
    standard = models.CharField(max_length=200, blank=True, verbose_name='ГОСТ/ОСТ')
    unit = models.CharField(max_length=20, default='шт', verbose_name='Ед. изм.')
    density = models.FloatField(null=True, blank=True, verbose_name='Плотность')
    notes = models.TextField(blank=True, verbose_name='Примечания')

    class Meta:
        verbose_name = 'Материал'
        verbose_name_plural = 'Материалы'

    def __str__(self):
        return self.name

class Employee(models.Model):
    last_name = models.CharField(max_length=100, verbose_name='Фамилия')
    first_name = models.CharField(max_length=100, verbose_name='Имя')
    middle_name = models.CharField(max_length=100, blank=True, verbose_name='Отчество')
    position = models.CharField(max_length=150, blank=True, verbose_name='Должность')
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='employee')
    is_active = models.BooleanField(default=True)
    ROLE_CHOICES = [
        ('admin', 'Администратор'),
        ('worker', 'Рабочий'),
        ('supervisor', 'Руководитель'),
        ('technologist', 'Технолог'),
        ('dispatcher', 'Диспетчер'),
        ('master', 'Мастер'),
        ('controller', 'Контролёр'),
        ('storekeeper', 'Кладовщик'),
        ('purchase_storekeeper', 'Кладовщик стандартных изделий'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='worker', verbose_name='Роль')

    class Meta:
        verbose_name = 'Сотрудник'
        verbose_name_plural = 'Сотрудники'

    def __str__(self):
        return f"{self.last_name} {self.first_name} {self.middle_name or ''}"

class OperationType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'Тип операции'
        verbose_name_plural = 'Типы операций'

class Item(models.Model):
    item_number = models.CharField(max_length=100, unique=True, verbose_name='Обозначение')
    name = models.CharField(max_length=255, verbose_name='Наименование')
    item_type = models.CharField(max_length=50, default='Деталь', verbose_name='Тип')
    material = models.ForeignKey(Material, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='Материал')
    profile = models.CharField(max_length=200, blank=True, verbose_name='Профиль/сортамент')
    blank_size = models.CharField(max_length=200, blank=True, verbose_name='Размер заготовки')
    blanks_per_item = models.PositiveIntegerField(default=1, verbose_name='Кол-во заготовок')

    def __str__(self):
        return f"{self.item_number} - {self.name}"

    class Meta:
        verbose_name = 'Изделие'
        verbose_name_plural = 'Изделия'

class Order(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Черновик'),
        ('in_progress', 'В работе'),
        ('paused', 'Приостановлен'),
        ('completed', 'Завершён'),
        ('shipped', 'Отгружен'),
        ('closed', 'Закрыт'),
    ]
    order_number = models.CharField(max_length=100, verbose_name='Номер договора')
    full_name = models.CharField(max_length=500, unique=True, null=True, blank=True, verbose_name='Полное наименование')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    due_date = models.DateField(null=True, blank=True, verbose_name='Дата отгрузки')
    project = models.CharField(max_length=200, blank=True, verbose_name='Проект')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', verbose_name='Статус')

    def __str__(self):
        return f"Заказ {self.order_number}"

    
    color1 = models.CharField(max_length=7, default='#ffffff', verbose_name='Цвет 1')
    color2 = models.CharField(max_length=7, default='#ffffff', verbose_name='Цвет 2')

    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items', verbose_name='Заказ')
    item = models.ForeignKey(Item, on_delete=models.CASCADE, verbose_name='Изделие')
    quantity = models.PositiveIntegerField(default=1, verbose_name='План на сборку')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children', verbose_name='Родительская позиция')

    

    def total_planned_quantity(self):
        """Общий план производства: план на сборку + настроечные"""

    def planned_quantity(self):
        return self.quantity

    def __str__(self):
        return f"{self.item.item_number} x{self.quantity} (Заказ {self.order.order_number})"

    class Meta:
        verbose_name = 'Позиция заказа'
        verbose_name_plural = 'Позиции заказов'

class ItemInstance(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='instances', verbose_name='Изделие')
    serial = models.CharField(max_length=200, unique=True, verbose_name='Серийный номер')
    quantity = models.PositiveIntegerField(default=1, verbose_name='План на сборку')
    blank_size = models.CharField(max_length=200, blank=True, default='', verbose_name='Размер заготовки (экз.)')
    blanks_per_item = models.PositiveIntegerField(null=True, blank=True, verbose_name='Кол-во заготовок (экз.)')
    setup_quantity = models.PositiveIntegerField(default=0, verbose_name='Настроечные')
    order_item = models.ForeignKey(OrderItem, null=True, blank=True, on_delete=models.SET_NULL, related_name='instances', verbose_name='Позиция заказа')
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name='instances', verbose_name='Договор')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    due_date = models.DateField(null=True, blank=True, verbose_name='Дата отгрузки')
    project = models.CharField(max_length=200, blank=True, verbose_name='Проект')

    

    def get_blank_size(self):
        return self.blank_size or (self.item.blank_size if self.item else '')

    def get_blanks_per_item(self):
        if self.blanks_per_item is not None:
            return self.blanks_per_item
        return self.item.blanks_per_item if self.item else 1

    def total_planned_quantity(self):
        """Общий план производства: план на сборку + настроечные"""
        return self.quantity + self.setup_quantity

    def planned_quantity(self):
        if self.order_item:
            return self.order_item.planned_quantity()
        return self.quantity

    def good_produced(self):
        if hasattr(self, 'route_card') and self.route_card:
            ops = self.route_card.operations.all()
            if ops:
                return min(op.good_qty for op in ops)
        return 0

    def completion_percent(self):
        # Считаем процент завершённых операций
        if hasattr(self, 'route_card') and self.route_card:
            ops = self.route_card.operations.all()
            if ops:
                completed = ops.filter(status='completed').count()
                return int(completed / ops.count() * 100)
        return 0

    
    def total_good_produced(self):
        """Суммарный выпуск годных по всем экземплярам этой позиции заказа"""
        if not self.order_item:
            return self.good_produced()
        total = 0
        for inst in self.order_item.instances.all():
            total += inst.good_produced()
        return total

    def shortage(self):
        return max(0, self.quantity - self.good_produced())

    def all_components_ready(self):
        """Проверяет, что все дочерние позиции заказа имеют готовые экземпляры"""
        if not self.order_item:
            return True
        children = self.order_item.children.all()
        if not children:
            return True
        for child in children:
            # для типов 'Деталь', 'Сборочная единица' нужен экземпляр
            if child.item.item_type in ('Деталь', 'Сборочная единица'):
                # Суммируем выпуск по всем экземплярам (включая дозапуски)
                total_good = sum(inst.good_produced() for inst in child.instances.all())
                if total_good < child.planned_quantity():
                    return False
            # для стандартных/покупных считаем, что они всегда есть на складе
        return True

    def assembly_status(self):
        if self.item.item_type != 'Сборочная единица':
            return None
        if not self.all_components_ready():
            return 'Ожидает комплектации'
        if not hasattr(self, 'route_card') or self.route_card.operations.count() == 0:
            return 'Готово к комплектованию'
        ops = self.route_card.operations.all()
        if ops.filter(status='in_progress').exists():
            return 'В работе'
        if ops.filter(status='pending').exists() and not ops.filter(status='completed').exists():
            return 'Готово к комплектованию'  # ещё не начинали
        if self.good_produced() >= self.planned_quantity():
            return 'Изготовлена'
        return 'В работе'  # частично завершена

    def current_operation_name(self):
        if not hasattr(self, 'route_card') or not self.route_card:
            return '—'
        ops = self.route_card.operations.order_by('order')
        in_progress = ops.filter(status='in_progress').first()
        if in_progress:
            return f'Выполняется: {in_progress.operation_type.name}'
        pending = ops.filter(status='pending').first()
        if pending:
            return f'Ожидает: {pending.operation_type.name}'
        if self.good_produced() == 0:
            return 'Требуется доп.запуск'
        return 'Изготовлена'

    class Meta:
        verbose_name = 'Экземпляр'
        verbose_name_plural = 'Экземпляры'

    
    
    def display_serial(self):
        """Возвращает серийный номер без системного суффикса (после последнего дефиса)."""
        parts = self.serial.rsplit('-', 1)
        if len(parts) > 1 and parts[1].isdigit():
            return parts[0]
        return self.serial

    def __str__(self):
        return f"{self.item.item_number} - {self.serial}"

class RouteCard(models.Model):
    instance = models.OneToOneField(ItemInstance, on_delete=models.CASCADE, related_name='route_card', verbose_name='Экземпляр')

    
    def operation_progress(self):
        total = self.operations.count()
        if total == 0:
            return 0
        completed = self.operations.filter(status='completed').count()
        return int(completed / total * 100)
    
    def get_status(self):
        ops = self.operations.order_by('order')
        total = ops.count()
        if total == 0:
            return {'code': 'empty', 'label': 'Маршрут не задан', 'progress': 0}
        for op in ops:
            if op.status == 'in_progress':
                return {'code': 'in_progress', 'label': f'Выполняется: {op.operation_type.name}', 'progress': 0}
        for op in ops:
            if op.status == 'pending':
                return {'code': 'pending', 'label': f'Ожидает: {op.operation_type.name}', 'progress': 0}
        return {'code': 'completed', 'label': 'Изготовлена', 'progress': 100}

    def __str__(self):
        return f"Маршрутная карта {self.instance.item.item_number} ({self.instance.serial})"

    class Meta:
        verbose_name = 'Маршрутная карта'
        verbose_name_plural = 'Маршрутные карты'

class RouteOperation(models.Model):
    route_card = models.ForeignKey(RouteCard, on_delete=models.CASCADE, related_name='operations', verbose_name='Маршрутная карта')
    operation_type = models.ForeignKey(OperationType, on_delete=models.CASCADE, verbose_name='Тип операции')
    order = models.PositiveIntegerField(default=0, verbose_name='Порядок')
    planned_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0, verbose_name='Норма часов')
    status = models.CharField(max_length=20, choices=[
        ('pending', 'Ожидает'),
        ('in_progress', 'В работе'),
        ('completed', 'Завершена')
    ], default='pending', verbose_name='Статус')
    started_at = models.DateTimeField(null=True, blank=True, verbose_name='Начало')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Завершение')
    worker = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='Исполнитель')
    worker_log = models.TextField(blank=True, verbose_name='История исполнителей')
    good_qty = models.PositiveIntegerField(default=0, verbose_name='Годных')
    bad_qty = models.PositiveIntegerField(default=0, verbose_name='Брак')
    notes = models.TextField(blank=True, verbose_name='Примечания')

    def save(self, *args, **kwargs):
        # автоматически устанавливаем порядок, если не задан явно
        if not self.order and self.route_card_id:
            last = self.route_card.operations.order_by('-order').first()
            self.order = (last.order + 1) if last else 1
        super().save(*args, **kwargs)

    
    def duration(self):
        if self.started_at and self.completed_at:
            delta = self.completed_at - self.started_at
            hours, remainder = divmod(delta.seconds, 3600)
            minutes = remainder // 60
            return f'{hours} ч {minutes} мин'
        return None
    
    class Meta:
        ordering = ['route_card', 'order']
        verbose_name = 'Операция маршрутной карты'
        verbose_name_plural = 'Операции маршрутных карт'

    def __str__(self):
        return f"Операция {self.order} ({self.operation_type.name}) для {self.route_card.instance.item.name}"


class WarehouseRecord(models.Model):
    MOVEMENT_TYPES = [
        ('in', 'Приход'),
        ('out', 'Расход'),
    ]
    instance = models.ForeignKey(ItemInstance, on_delete=models.CASCADE, related_name='warehouse_records', verbose_name='Партия')
    movement_type = models.CharField(max_length=3, choices=MOVEMENT_TYPES, verbose_name='Тип операции')
    quantity = models.PositiveIntegerField(verbose_name='План на сборку')
    date = models.DateTimeField(default=timezone.now, verbose_name='Дата')
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='Сотрудник')
    recipient = models.CharField(max_length=200, blank=True, verbose_name='Получатель')
    basis = models.CharField(max_length=300, blank=True, verbose_name='Основание')
    notes = models.TextField(blank=True, verbose_name='Примечания')

    class Meta:
        verbose_name = 'Складская запись'
        verbose_name_plural = 'Складские записи'

    def __str__(self):
        return f"{self.get_movement_type_display()} {self.instance.item.item_number} x{self.quantity}"



from django.db.models.signals import pre_delete
from django.dispatch import receiver

@receiver(pre_delete, sender=Order)
def delete_order_instances(sender, instance, **kwargs):
    # Удаляем все экземпляры, связанные с этим заказом
    ItemInstance.objects.filter(order=instance).delete()

from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=Employee)
def assign_group_on_role_change(sender, instance, created, **kwargs):
    if instance.user:
        # Убираем пользователя из всех групп, связанных с ролями
        role_groups = ['Диспетчер', 'Технолог', 'Кладовщик', 'Контролёр', 'Мастер', 'Рабочий', 'Руководитель']
        for g in instance.user.groups.filter(name__in=role_groups):
            instance.user.groups.remove(g)
        # Добавляем в нужную группу по текущей роли
        group_name = dict(Employee.ROLE_CHOICES).get(instance.role)
        if group_name:
            from django.contrib.auth.models import Group
            group = Group.objects.get(name=group_name)
            instance.user.groups.add(group)

from scanner.purchase_models import PurchaseItem, PurchaseTransaction
