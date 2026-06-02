from django.urls import path
from . import views

urlpatterns = [
    path('order/<int:order_id>/due-date/', views.update_order_due_date, name='update_order_due_date'),
    path('order/<int:order_id>/status/<str:new_status>/', views.change_order_status, name='change_order_status'),
    path('orders-control/', views.orders_control, name='orders_control'),
    path('search/', views.search, name='search'),
    path('restore/', views.restore_backup, name='restore_backup'),
    path('order/create/', views.order_create, name='order_create'),
    path('stats/compare/', views.statistics_compare, name='stats_compare'),
    path('stats/export/', views.statistics_export, name='stats_export'),
    path('stats/operations/<str:type_name>/export/', views.statistics_operations_export, name='stats_operations_export'),
    path('stats/operations/<str:type_name>/', views.statistics_operations, name='stats_operations'),
    path('stats/', views.statistics, name='stats'),
    path('', views.dashboard, name='home'),
    path('orders/', views.order_list, name='order_list'),
    path('order/<int:order_id>/', views.order_detail, name='order_detail'),
    path('order/<int:order_id>/tree/', views.order_tree, name='order_tree'),
    path('order/<int:order_id>/import/', views.order_import, name='order_import'),
    path('instance/<str:item_number>/<str:serial>/', views.instance_detail, name='instance_detail'),
    path('supplement/<int:instance_id>/', views.supplement_instance, name='supplement_instance'),
    path('route-card/<int:route_card_id>/print/', views.route_card_print, name='route_card_print'),
    path('route-card/<int:route_card_id>/export/', views.route_card_export, name='route_card_export'),
    path('route-card/create/<int:instance_id>/', views.route_card_create, name='route_card_create'),

    path('warehouse/', views.warehouse_dashboard, name='warehouse'),
    path('warehouse/issue/', views.warehouse_issue, name='warehouse_issue'),
]
