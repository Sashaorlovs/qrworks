from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from scanner.material_models import (
    MaterialGrade,
    MaterialRequirement,
    MaterialStockLot,
    MaterialTransaction,
)
from scanner.material_services import create_material_request, issue_request_lines
from scanner.models import Employee, Order


class MaterialCalculationTests(TestCase):
    def setUp(self):
        self.steel = MaterialGrade.objects.create(name="Тестовая сталь", category="steel", density_kg_m3=7850)

    def test_sheet_area_and_mass(self):
        item = MaterialRequirement(
            grade=self.steel,
            profile_type="sheet",
            thickness_mm=8,
            width_mm=1000,
            piece_length_mm=2000,
        )
        self.assertEqual(item.calculate_area_m2(2), Decimal("4.000"))
        self.assertEqual(item.calculate_mass(2, 0), Decimal("251.200"))

    def test_round_pipe_mass_by_total_remaining_length(self):
        item = MaterialRequirement(
            grade=self.steel,
            profile_type="round_pipe",
            outer_diameter_mm=57,
            wall_thickness_mm=3.5,
        )
        self.assertEqual(item.calculate_mass(0, 12000), Decimal("55.414"))


class MaterialIssueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="material-test", password="x")
        Employee.objects.bulk_create([Employee(last_name="Иванов", first_name="Иван", is_active=True)])
        self.employee = Employee.objects.get(last_name="Иванов")
        self.order = Order.objects.create(order_number="TEST-1", full_name="Тестовый проект")
        self.steel = MaterialGrade.objects.create(name="Сталь для выдачи", category="steel", density_kg_m3=7850)
        self.requirement = MaterialRequirement.objects.create(
            order=self.order,
            item_name="Труба на раму",
            grade=self.steel,
            profile_type="round_pipe",
            outer_diameter_mm=57,
            wall_thickness_mm=3.5,
            piece_length_mm=3000,
            quantity_required=2,
            total_length_required_mm=6000,
        )
        self.lot = MaterialStockLot(
            order=self.order,
            name="Труба 57×3,5",
            grade=self.steel,
            profile_type="round_pipe",
            outer_diameter_mm=57,
            wall_thickness_mm=3.5,
            piece_length_mm=6000,
            quantity_initial=2,
            length_initial_mm=12000,
            created_by=self.user,
        )
        self.lot.initialize_balances()
        self.lot.save()

    def test_partial_issue_updates_length_mass_and_document(self):
        document = create_material_request(self.order, [self.requirement], self.user, "Цех 1")
        line = document.lines.get()
        token, rows = issue_request_lines(
            document,
            {line.id: {"length_mm": "3000", "quantity": "0"}},
            self.employee,
            "На раму",
            self.user,
        )
        self.assertTrue(token)
        self.assertEqual(len(rows), 1)
        self.lot.refresh_from_db()
        document.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(self.lot.length_remaining_mm, Decimal("9000"))
        self.assertEqual(line.length_issued_mm, Decimal("3000"))
        self.assertEqual(document.status, "partial")
        self.assertEqual(MaterialTransaction.objects.filter(transaction_type="out").count(), 1)

    def test_overissue_rolls_back_stock(self):
        document = create_material_request(self.order, [self.requirement], self.user)
        line = document.lines.get()
        with self.assertRaises(ValidationError):
            issue_request_lines(
                document,
                {line.id: {"length_mm": "7000", "quantity": "0"}},
                self.employee,
                "",
                self.user,
            )
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.length_remaining_mm, Decimal("12000"))
        self.assertFalse(MaterialTransaction.objects.filter(transaction_type="out").exists())
