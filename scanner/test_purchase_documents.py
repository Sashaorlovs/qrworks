from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from scanner.models import Employee, Order
from scanner.purchase_allocation import allocation_state
from scanner.purchase_documents import (
    create_purchase_request,
    general_surplus_state,
    issue_general_surplus,
    issue_purchase_request,
)
from scanner.purchase_models import PurchaseItem, PurchaseTransaction


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
