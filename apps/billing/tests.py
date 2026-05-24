from django.test import TestCase
from django.contrib.auth import get_user_model
from decimal import Decimal
from datetime import date, timedelta
from rest_framework.test import APIClient

from apps.billing.models import Invoice, InvoiceLineItem, Quotation, RecurringInvoiceSchedule
from apps.billing.serializers import InvoiceSerializer
from apps.projects.models import Project, TaskStatus, Task, TimeEntry

User = get_user_model()

class BillingCalculationTests(TestCase):
    def setUp(self):
        self.client = User.objects.create_user(
            email='client@billing.com',
            password='testpassword123',
            category='external'
        )

    def test_invoice_serializer_calculations(self):
        # We will post nested line items and verify mathematical calculations
        data = {
            'invoice_number': 'INV-2026-9999',
            'client': self.client.id,
            'issue_date': date(2026, 5, 22),
            'due_date': date(2026, 6, 22),
            'tax_percentage': Decimal('10.00'), # 10% tax
            'discount_total': Decimal('50.00'), # $50 discount
            'line_items': [
                {
                    'type': 'Fixed',
                    'description': 'Software Consulting',
                    'qty': Decimal('2.00'),
                    'rate': Decimal('100.00') # $200 total
                },
                {
                    'type': 'Time',
                    'description': 'DevOps Support',
                    'qty': Decimal('5.00'),
                    'rate': Decimal('50.00') # $250 total
                }
            ]
        }
        
        # Subtotal = 200 + 250 = $450
        # Discount = $50
        # Net = 450 - 50 = $400
        # Tax = 10% of 400 = $40
        # Total = $440
        
        serializer = InvoiceSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        invoice = serializer.save()
        
        self.assertEqual(invoice.subtotal, Decimal('450.00'))
        self.assertEqual(invoice.total_amount, Decimal('440.00'))
        
        # Verify line item records are created properly
        self.assertEqual(invoice.line_items.count(), 2)
        line_consulting = invoice.line_items.filter(description='Software Consulting').first()
        self.assertEqual(line_consulting.amount, Decimal('200.00'))


class BillingWorkflowActionTests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.owner = User.objects.create_user(
            email='owner@agency.com',
            password='Password123!',
            category='agency',
            is_superuser=True,
        )
        self.client_user = User.objects.create_user(
            email='client@agency.com',
            password='Password123!',
            category='external',
        )
        self.project = Project.objects.create(name='Billing Project')
        self.status = TaskStatus.objects.create(project=self.project, name='In Progress', order=1)
        self.task = Task.objects.create(project=self.project, status=self.status, title='Implementation', is_billable=True)
        TimeEntry.objects.create(task=self.task, user=self.owner, duration_minutes=180)

        self.client_api.force_authenticate(user=self.owner)

    def test_create_invoice_from_quotation(self):
        quotation = Quotation.objects.create(
            client=self.client_user,
            project=self.project,
            title='Website redesign quote',
            total_amount=Decimal('1200.00'),
            status='accepted',
        )

        response = self.client_api.post(
            f'/api/quotations/{quotation.id}/create_invoice/',
            {'tax_percentage': '5.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Invoice.objects.count(), 1)
        invoice = Invoice.objects.first()
        self.assertEqual(invoice.client_id, self.client_user.id)
        self.assertEqual(invoice.line_items.count(), 1)

    def test_create_invoice_from_project_billable_tasks(self):
        response = self.client_api.post(
            '/api/invoices/from_project/',
            {
                'project_id': str(self.project.id),
                'client_id': str(self.client_user.id),
                'hourly_rate': '100.00',
                'tax_percentage': '0.00',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        invoice = Invoice.objects.get(id=response.data['id'])
        self.assertEqual(invoice.subtotal, Decimal('300.00'))
        self.assertEqual(invoice.line_items.count(), 1)

    def test_run_due_recurring_schedule_generates_invoice(self):
        RecurringInvoiceSchedule.objects.create(
            client=self.client_user,
            project=self.project,
            title='Monthly support retainer',
            amount=Decimal('900.00'),
            interval='monthly',
            next_run_date=date.today() - timedelta(days=1),
            tax_percentage=Decimal('0.00'),
            active=True,
            auto_send=False,
        )

        response = self.client_api.post('/api/recurring-schedules/run_due/', {}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_checkout_session_returns_mock_without_stripe_sdk(self):
        invoice = Invoice.objects.create(
            invoice_number='INV-2026-5000',
            client=self.client_user,
            project=self.project,
            issue_date=date.today(),
            due_date=date.today(),
            subtotal=Decimal('100.00'),
            tax_percentage=Decimal('0.00'),
            discount_total=Decimal('0.00'),
            total_amount=Decimal('100.00'),
            status='sent',
        )

        response = self.client_api.post(f'/api/invoices/{invoice.id}/create_checkout_session/', {}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn('checkout_url', response.data)
        self.assertIn('mode', response.data)
