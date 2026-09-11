from django.urls import path

from scanner import material_views


urlpatterns = [
    path("", material_views.material_home, name="material_home"),
    path("import/", material_views.material_import, name="material_import"),
    path("template/", material_views.material_import_template, name="material_import_template"),
    path("order/<int:order_id>/", material_views.material_order_detail, name="material_order_detail"),
    path("order/<int:order_id>/request/", material_views.material_order_create_request, name="material_order_create_request"),
    path("stock/", material_views.material_stock, name="material_stock"),
    path("requests/", material_views.material_requests, name="material_requests"),
    path("request/<int:request_id>/", material_views.material_request_detail, name="material_request_detail"),
    path("request/<int:request_id>/print/", material_views.material_request_print, name="material_request_print"),
    path("issues/", material_views.material_issue_log, name="material_issue_log"),
    path("issue/<str:batch_token>/print/", material_views.material_issue_print, name="material_issue_print"),
]
