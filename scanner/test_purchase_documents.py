from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from scanner.models import Employee, Order
from scanner.material_models import WarehouseIssuer
from scanner.purchase_allocation import allocation_state
from scanner.purchase_documents import (
    create_purchase_request,
    general_surplus_state,
    issue_general_surplus,
    issue_purchase_request,
)
from scanner.purchase_models import PurchaseItem, PurchasePreparation, PurchaseRequest, PurchaseTransaction


class PurchaseDocumentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='purchase-doc-test', password='x')
        self.employee = Employee.objects.create(
            last_name='Иванов', first_name='Иван', is_active=True
        )
        self.order = Order.objects.create(order_number='FAST-1', full_name='Тестовый заказ')
        self.item = PurchaseItem.objects.create(
            order=self.order,
            item_name='Болт М10',
            assembly_name='Сборка 1',
            quantity_required=5,
            quantity_purchased=10,
            purchase_status='ready_for_issue',
        )

    def test_request_gets_number_and_does_not_duplicate_open_demand(self):
        document = create_purchase_request(
            self.order, [self.item.id], self.user, ''
        )
        self.assertTrue(document.number.startswith('КЗ-'))
        self.assertEqual(document.purpose, 'Сборка 1')
        self.assertEqual(document.lines.get().quantity_requested, 5)
        with self.assertRaises(ValidationError):
            create_purchase_request(self.order, [self.item.id], self.user)

    def test_partial_issue_gets_stable_invoice_number(self):
        document = create_purchase_request(self.order, [self.item.id], self.user)
        line = document.lines.get()
        token, number, rows = issue_purchase_request(
            document, {line.id: '2'}, self.employee, 'Сборочный участок', self.user,
            self.employee,
        )
        self.assertTrue(token)
        self.assertTrue(number.startswith('НК-'))
        self.assertEqual(len(rows), 1)
        movement = PurchaseTransaction.objects.get()
        self.assertEqual(movement.document_number, number)
        self.assertEqual(movement.request_line_id, line.id)
        self.assertEqual(movement.issuer_name, str(self.employee).strip())
        self.assertEqual(number, 'НК-2026-000001')
        document.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(document.status, 'partial')
        self.assertEqual(line.quantity_issued, 2)

    def test_request_overissue_is_rolled_back(self):
        document = create_purchase_request(self.order, [self.item.id], self.user)
        line = document.lines.get()
        with self.assertRaises(ValidationError):
            issue_purchase_request(
                document, {line.id: '6'}, self.employee, '', self.user
            )
        self.assertFalse(PurchaseTransaction.objects.exists())

    def test_only_unreserved_surplus_can_go_to_general_use(self):
        state = general_surplus_state(self.item)
        self.assertEqual(state['general_available'], 5)
        token, number, movement = issue_general_surplus(
            self.item.id, 5, self.employee, 'Общепроизводственные нужды', self.user,
            self.employee,
        )
        self.assertTrue(token)
        self.assertTrue(number.startswith('НК-'))
        self.assertTrue(movement.is_general_use)
        self.assertEqual(movement.issuer_name, str(self.employee).strip())
        self.assertEqual(allocation_state(self.item).demand_available, 5)
        self.assertEqual(allocation_state(self.item).stock_available, 5)

    def test_reserved_quantity_cannot_go_to_general_use(self):
        with self.assertRaises(ValidationError):
            issue_general_surplus(
                self.item.id, 6, self.employee, 'Общепроизводственные нужды', self.user
            )
        self.assertFalse(PurchaseTransaction.objects.exists())

    def test_non_ready_item_cannot_be_issued(self):
        self.item.purchase_status = 'pending'
        self.item.save(update_fields=['purchase_status'])
        with self.assertRaises(ValidationError):
            issue_general_surplus(
                self.item.id, 1, self.employee, 'Общепроизводственные нужды', self.user
            )
        document = create_purchase_request(self.order, [self.item.id], self.user)
        line = document.lines.get()
        with self.assertRaises(ValidationError):
            issue_purchase_request(
                document, {line.id: '1'}, self.employee, '', self.user
            )

    def test_fractional_quantity_can_be_requested_and_issued(self):
        self.item.quantity_required = '0.540'
        self.item.quantity_purchased = '1.000'
        self.item.save(update_fields=['quantity_required', 'quantity_purchased'])
        document = create_purchase_request(self.order, [self.item.id], self.user)
        line = document.lines.get()
        issue_purchase_request(document, {line.id: '0,250'}, self.employee, '', self.user)
        line.refresh_from_db()
        self.assertEqual(str(line.quantity_issued), '0.250')


class PurchaseWorkflowViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='purchase-admin', password='x')
        self.client.force_login(self.user)
        self.order = Order.objects.create(order_number='BIND-1', full_name='Заказ для привязки')

    def test_open_request_can_be_cancelled_with_reason(self):
        document = PurchaseRequest.objects.create(number='КЗ-TEST-001', order=self.order, requested_by=self.user)
        response = self.client.post(f'/purchases/request/{document.id}/cancel/', {'cancellation_reason': 'Создана ошибочно'})
        self.assertRedirects(response, f'/purchases/request/{document.id}/')
        document.refresh_from_db()
        self.assertEqual(document.status, 'cancelled')
        self.assertEqual(document.cancellation_reason, 'Создана ошибочно')
        self.assertEqual(document.cancelled_by, self.user)

    def test_request_page_uses_independent_warehouse_issuer_list(self):
        WarehouseIssuer.objects.get_or_create(name='Макарова Оксана Михайловна')
        WarehouseIssuer.objects.get_or_create(name='Чувашлева Екатерина Юрьевна')
        document = PurchaseRequest.objects.create(number='КЗ-TEST-ISSUER', order=self.order, requested_by=self.user)
        response = self.client.get(f'/purchases/request/{document.id}/')
        self.assertContains(response, 'Кто отпустил')
        self.assertContains(response, 'Макарова Оксана Михайловна')
        self.assertContains(response, 'Чувашлева Екатерина Юрьевна')

    def test_blank_preparation_can_be_created_before_order_exists(self):
        response = self.client.post('/purchases/', {'preparation_name': 'Будущий проект'})
        preparation = PurchasePreparation.objects.get(name='Будущий проект')
        self.assertRedirects(response, f'/purchases/preparation/{preparation.id}/')
        self.assertIsNone(preparation.assigned_order)

    def test_preparation_is_bound_without_losing_quantities(self):
        preparation = PurchasePreparation.objects.create(name='Станция гидравлическая', created_by=self.user)
        item = PurchaseItem.objects.create(
            preparation=preparation, item_name='Рукав DN10', assembly_name='Узел 1',
            quantity_required='0.540', quantity_purchased='1.000',
        )
        response = self.client.post(f'/purchases/preparation/{preparation.id}/', {'action': 'bind', 'order_id': self.order.id})
        self.assertRedirects(response, f'/purchases/{self.order.id}/')
        item.refresh_from_db()
        preparation.refresh_from_db()
        self.assertEqual(item.order, self.order)
        self.assertEqual(str(item.quantity_required), '0.540')
        self.assertEqual(str(item.quantity_purchased), '1.000')
        self.assertEqual(preparation.assigned_order, self.order)
