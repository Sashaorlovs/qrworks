from django.contrib import admin
from django.utils.safestring import mark_safe
from .models import (
    Employee, OperationType, Item, Order, OrderItem,
    ItemInstance, RouteCard, RouteOperation,
    WarehouseRecord
)

# ------------------ Action для создания учётных записей ------------------
def create_user_accounts(modeladmin, request, queryset):
    from django.contrib.auth.models import User
    from django.utils.crypto import get_random_string

    created = 0
    for emp in queryset:
        if emp.user:
            continue
        # транслитерация
        translit = {
            'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i','й':'y',
            'к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f',
            'х':'h','ц':'ts','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
            'А':'A','Б':'B','В':'V','Г':'G','Д':'D','Е':'E','Ё':'E','Ж':'Zh','З':'Z','И':'I','Й':'Y',
            'К':'K','Л':'L','М':'M','Н':'N','О':'O','П':'P','Р':'R','С':'S','Т':'T','У':'U','Ф':'F',
            'Х':'H','Ц':'Ts','Ч':'Ch','Ш':'Sh','Щ':'Sch','Ъ':'','Ы':'Y','Ь':'','Э':'E','Ю':'Yu','Я':'Ya'
        }
        last_latin = ''.join(translit.get(ch, ch) for ch in emp.last_name.lower()).title()
        initials = (emp.first_name[0] if emp.first_name else '') + (emp.middle_name[0] if emp.middle_name else '')
        username = f"{last_latin}{initials}".lower()
        base = username
        i = 1
        while User.objects.filter(username=username).exists():
            username = f"{base}{i}"
            i += 1
        password = get_random_string(length=10)
        user = User.objects.create_user(username=username, password=password,
                                        first_name=emp.first_name, last_name=emp.last_name)
        emp.user = user
        emp.save(update_fields=['user'])
        # Устанавливаем is_staff для ролей, которым нужен доступ в админку
        if emp.role in ['admin', 'dispatcher', 'technologist']:
            user.is_staff = True
            user.save()
        # Сигнал в models.py автоматически добавит пользователя в нужную группу при сохранении Employee
        created += 1
        modeladmin.message_user(request, f'{emp}: создана учётная запись {username}')
    if created == 0:
        modeladmin.message_user(request, 'У всех выбранных сотрудников уже есть учётные записи.')

create_user_accounts.short_description = 'Создать учётные записи'

# ------------------ EmployeeAdmin ------------------
class EmployeeAdmin(admin.ModelAdmin):
    actions_on_top = True
    actions_on_bottom = True
    list_display = ('last_name', 'first_name', 'middle_name', 'position', 'role', 'has_account', 'is_active')
    list_filter = ('is_active', 'position')
    search_fields = ('last_name', 'first_name', 'middle_name')
    actions = [create_user_accounts]

    @admin.display(description='Учётная запись')
    def has_account(self, obj):
        if obj.user:
            return mark_safe('<span style="color:green;">✅</span>')
        return mark_safe('<span style="color:red;">❌</span>')

# ------------------ Остальные модели ------------------
@admin.register(OperationType)
class OperationTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'code')
    search_fields = ('name', 'code')

class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'item', 'quantity', 'parent')
    list_filter = ('order',)
    search_fields = ('item__item_number', 'item__name')

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ('item', 'quantity', 'parent')

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'full_name', 'status', 'created_at')
    list_editable = ('full_name', 'status')
    list_filter = ('status',)
    search_fields = ('order_number', 'full_name')
    inlines = [OrderItemInline]

@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('item_number', 'name', 'item_type')
    search_fields = ('item_number', 'name')

@admin.register(ItemInstance)
class ItemInstanceAdmin(admin.ModelAdmin):

    def has_change_permission(self, request, obj=None):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        try:
            role = request.user.employee.role
            if role in ('admin', 'dispatcher'):
                return True
        except:
            pass
        return False

    def has_view_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)

    def has_add_permission(self, request):
        return self.has_change_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)

    list_display = ('serial', 'item', 'quantity', 'order', 'created_at')
    list_filter = ('order',)
    search_fields = ('serial', 'item__item_number', 'item__name')

class RouteOperationInline(admin.TabularInline):
    model = RouteOperation
    extra = 1
    fields = ('operation_type', 'planned_hours', 'status', 'good_qty', 'bad_qty', 'worker')

    def get_readonly_fields(self, request, obj=None):
        # Разрешаем редактировать good_qty и bad_qty только администратору
        if request.user.is_superuser:
            return ()
        # Проверяем роль через Employee
        try:
            if request.user.employee.role == 'admin':
                return ()
        except:
            pass
        return ('good_qty', 'bad_qty')

@admin.register(RouteCard)
class RouteCardAdmin(admin.ModelAdmin):
    list_display = ('instance_link', 'get_operations_count')
    search_fields = ('instance__item__item_number', 'instance__serial')
    inlines = [RouteOperationInline]

    @admin.display(description='Экземпляр')
    def instance_link(self, obj):
        from django.utils.html import format_html
        # Ссылка на редактирование маршрутной карты
        url = f"/admin/scanner/routecard/{obj.id}/change/"
        return format_html('<a href="{}">{}</a>', url, f"{obj.instance.item.item_number} - {obj.instance.serial}")

    @admin.display(description='Операций')
    def get_operations_count(self, obj):
        return obj.operations.count()

@admin.register(RouteOperation)
class RouteOperationAdmin(admin.ModelAdmin):
    list_display = ('route_card_link', 'operation_type', 'order', 'status', 'good_qty', 'bad_qty', 'notes')
    list_filter = ('status',)
    search_fields = ('route_card__instance__serial')

    @admin.display(description='Маршрутная карта')
    def route_card_link(self, obj):
        from django.utils.html import format_html
        url = f"/admin/scanner/routecard/{obj.route_card.id}/change/"
        return format_html('<a href="{}">{}</a>', url, str(obj.route_card))

# Регистрируем Employee вручную (чтобы избежать конфликта с возможным дублированием)
admin.site.register(Employee, EmployeeAdmin)

@admin.register(WarehouseRecord)
class WarehouseRecordAdmin(admin.ModelAdmin):
    list_display = ('date', 'movement_type_verbose', 'instance_info', 'quantity', 'recipient', 'basis', 'employee', 'notes')
    list_filter = ('movement_type', 'date')
    search_fields = ('instance__item__item_number', 'instance__serial', 'recipient')

    @admin.display(description='Тип')
    def movement_type_verbose(self, obj):
        return obj.get_movement_type_display()

    @admin.display(description='Партия')
    def instance_info(self, obj):
        return f"{obj.instance.item.item_number} — {obj.instance.serial}"

admin.site.register(OrderItem, OrderItemAdmin)
