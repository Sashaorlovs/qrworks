from django.urls import path
from scanner.purchase_views import purchase_remains_issue, purchase_general_issue, purchase_issue_log, purchase_spec_list, purchase_spec_detail, purchase_import, purchase_import_template, purchase_change_status, purchase_remains, purchase_issue, purchase_issue_remains, purchase_bulk_issue, purchase_reprint_nakladnaya, purchase_export_request, purchase_requests, purchase_create_request, purchase_request_detail, purchase_request_print, purchase_invoice_print

urlpatterns = [
    path('', purchase_spec_list, name='purchase_list'),
    path('<int:spec_id>/', purchase_spec_detail, name='purchase_spec_detail'),
    path('import/', purchase_import, name='purchase_import'),
    path('import/template/', purchase_import_template, name='purchase_import_template'),
    path('change-status/', purchase_change_status, name='purchase_change_status'),
    path('remains/', purchase_remains, name='purchase_remains'),
    path('issue/', purchase_issue, name='purchase_issue'),
    path('issue-remains/', purchase_issue_remains, name='purchase_issue_remains'),
    path('bulk-issue/', purchase_bulk_issue, name='purchase_bulk_issue'),
    path('issue-log/', purchase_issue_log, name='purchase_issue_log'),
    path('reprint/<int:transaction_id>/', purchase_reprint_nakladnaya, name='purchase_reprint_nakladnaya'),
    path('remains-issue/', purchase_remains_issue, name='purchase_remains_issue'),
    path('general-issue/', purchase_general_issue, name='purchase_general_issue'),
    path('export-request/', purchase_export_request, name='purchase_export_request'),
    path('requests/', purchase_requests, name='purchase_requests'),
    path('request/<int:request_id>/', purchase_request_detail, name='purchase_request_detail'),
    path('request/<int:request_id>/print/', purchase_request_print, name='purchase_request_print'),
    path('order/<int:order_id>/request/', purchase_create_request, name='purchase_create_request'),
    path('invoice/<str:batch_token>/print/', purchase_invoice_print, name='purchase_invoice_print'),
]
