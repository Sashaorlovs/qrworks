from django.urls import path
from scanner.purchase_views import purchase_list, purchase_reprint_nakladnaya, purchase_import, purchase_change_status, purchase_remains, purchase_issue, purchase_bulk_issue

urlpatterns = [
    path('', purchase_list, name='purchase_list'),
    path('import/', purchase_import, name='purchase_import'),
    path('change-status/', purchase_change_status, name='purchase_change_status'),
    path('remains/', purchase_remains, name='purchase_remains'),
    path('issue/', purchase_issue, name='purchase_issue'),
    path('bulk-issue/', purchase_bulk_issue, name='purchase_bulk_issue'),
    path('reprint/<int:transaction_id>/', purchase_reprint_nakladnaya, name='purchase_reprint_nakladnaya'),
]
