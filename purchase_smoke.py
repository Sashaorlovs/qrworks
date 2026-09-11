import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'qr_simple.settings')

import django

django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from scanner.purchase_models import PurchaseItem, PurchaseRequest, PurchaseRequestLine, PurchaseTransaction


user = get_user_model().objects.filter(is_superuser=True).first()
assert user, 'No superuser found'
client = Client()
client.force_login(user)

item = PurchaseItem.objects.select_related('order').filter(order__isnull=False).first()
assert item, 'No purchase item found'

expected = {
    '/purchases/': 200,
    '/purchases/remains/': 200,
    '/purchases/issue/': 200,
    '/purchases/issue-log/': 200,
    '/purchases/requests/': 200,
    f'/purchases/{item.order_id}/': 200,
}
results = {url: client.get(url).status_code for url in expected}

with transaction.atomic():
    document = PurchaseRequest.objects.create(
        number='SMOKE-TEMP', order=item.order, purpose='Проверка', requested_by=user
    )
    PurchaseRequestLine.objects.create(
        request=document, purchase_item=item, quantity_requested=1
    )
    results[f'/purchases/request/{document.id}/'] = client.get(
        f'/purchases/request/{document.id}/'
    ).status_code
    results[f'/purchases/request/{document.id}/print/'] = client.get(
        f'/purchases/request/{document.id}/print/'
    ).status_code
    transaction.set_rollback(True)

movement = PurchaseTransaction.objects.filter(
    transaction_type='out'
).exclude(batch_token='').first()
if movement:
    results[f'/purchases/invoice/{movement.batch_token}/print/'] = client.get(
        f'/purchases/invoice/{movement.batch_token}/print/'
    ).status_code

for url, code in results.items():
    assert code == expected.get(url, 200), (url, code, expected.get(url, 200))
print('PURCHASE_SMOKE_OK', results)
client.logout()
