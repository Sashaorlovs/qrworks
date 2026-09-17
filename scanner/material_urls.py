from django.urls import path

from scanner import material_views


urlpatterns = [
    path("", material_views.material_home, name="material_home"),
    path("import/", material_views.material_import, name="material_import"),
    path("template/", material_views.material_import_template, name="material_import_template"),
    path("order/<int:order_id>/", material_views.material_order_detail, name="material_order_detail"),
    path("order/<int:order_id>/request/", material_views.material_order_create_request, name="material_order_create_request"),
    path("general/", material_views.material_general_detail, name="material_general_detail"),
    path("general/request/", material_views.material_general_create_request, name="material_general_create_request"),
    path("stock/", material_views.material_stock, name="material_stock"),
    path("stock/import/", material_views.material_stock_import, name="material_stock_import"),
    path("stock/import/template/", material_views.material_stock_import_template, name="material_stock_import_template"),
    path("stock/export/", material_views.material_stock_export, name="material_stock_export"),
    path("stock/auxiliary/receipt/", material_views.material_auxiliary_receipt, name="material_auxiliary_receipt"),
    path("stock/auxiliary/<int:lot_id>/issue/", material_views.material_auxiliary_issue, name="material_auxiliary_issue"),
    path("requests/", material_views.material_requests, name="material_requests"),
    path("request/<int:request_id>/", material_views.material_request_detail, name="material_request_detail"),
    path("request/<int:request_id>/reject/", material_views.material_request_reject, name="material_request_reject"),
    path("request/<int:request_id>/print/", material_views.material_request_print, name="material_request_print"),
    path("issues/", material_views.material_issue_log, name="material_issue_log"),
    path("issue/<str:batch_token>/print/", material_views.material_issue_print, name="material_issue_print"),
]
