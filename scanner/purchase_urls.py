from django.urls import path
from scanner.purchase_views import purchase_issue_log, purchase_spec_list, purchase_spec_detail, purchase_import, purchase_change_status, purchase_remains, purchase_issue, purchase_issue_remains, purchase_bulk_issue, purchase_reprint_nakladnaya, purchase_export_request

urlpatterns = [
    path('', purchase_spec_list, name='purchase_list'),
    path('<int:spec_id>/', purchase_spec_detail, name='purchase_spec_detail'),
    path('import/', purchase_import, name='purchase_import'),
    path('change-status/', purchase_change_status, name='purchase_change_status'),
    path('remains/', purchase_remains, name='purchase_remains'),
    path('issue/', purchase_issue, name='purchase_issue'),
    path('issue-remains/', purchase_issue_remains, name='purchase_issue_remains'),
    path('bulk-issue/', purchase_bulk_issue, name='purchase_bulk_issue'),
    path('issue-log/', purchase_issue_log, name='purchase_issue_log'),
    path('reprint/<int:transaction_id>/', purchase_reprint_nakladnaya, name='purchase_reprint_nakladnaya'),
    path('export-request/', purchase_export_request, name='purchase_export_request'),
]
