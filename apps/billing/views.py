from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import os

from django.db.models import Sum
from django.utils import timezone

from .models import Quotation, Invoice, InvoiceLineItem, RetainerAgreement, RecurringInvoiceSchedule, Expense
from .serializers import (
    QuotationSerializer, InvoiceSerializer, InvoiceLineItemSerializer,
    RetainerAgreementSerializer, RecurringInvoiceScheduleSerializer, ExpenseSerializer
)
from apps.authentication.permissions import HasTenantPermission
from apps.projects.models import Project, Task, TaskStatus


def _next_invoice_number(prefix='INV'):
    year = timezone.now().year
    base = f'{prefix}-{year}-'
    latest = Invoice.objects.filter(invoice_number__startswith=base).order_by('-created_at').first()
    if not latest:
        return f'{base}0001'
    try:
        last = int(latest.invoice_number.split('-')[-1])
    except (ValueError, IndexError):
        last = 0
    return f'{base}{last + 1:04d}'


def _calculate_total(subtotal, discount_total, tax_percentage):
    taxable = subtotal - discount_total
    multiplier = Decimal('1.00') + (tax_percentage / Decimal('100.00'))
    return (taxable * multiplier).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _bump_schedule_next_run(schedule):
    if schedule.interval == 'weekly':
        schedule.next_run_date = schedule.next_run_date + timedelta(days=7)
    elif schedule.interval == 'quarterly':
        schedule.next_run_date = schedule.next_run_date + timedelta(days=90)
    else:
        schedule.next_run_date = schedule.next_run_date + timedelta(days=30)

class QuotationViewSet(viewsets.ModelViewSet):
    queryset = Quotation.objects.all()
    serializer_class = QuotationSerializer
    required_permission = 'can_manage_billing'
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), HasTenantPermission()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Quotation.objects.all()
        role_name = user.role_obj.name if user.role_obj else ""
        if user.category == 'agency' and role_name in ['Owner', 'Agency Manager']:
            return Quotation.objects.all()
        # Clients can see their own quotations
        return Quotation.objects.filter(client=user)

    @action(detail=True, methods=['post'])
    def create_invoice(self, request, pk=None):
        quotation = self.get_object()
        due_in_days = int(request.data.get('due_in_days', 14))
        issue_date = timezone.now().date()
        due_date = issue_date + timedelta(days=due_in_days)

        invoice = Invoice.objects.create(
            invoice_number=request.data.get('invoice_number') or _next_invoice_number(),
            client=quotation.client,
            project=quotation.project,
            issue_date=issue_date,
            due_date=due_date,
            subtotal=quotation.total_amount,
            tax_percentage=Decimal(request.data.get('tax_percentage', '0.00')),
            discount_total=Decimal(request.data.get('discount_total', '0.00')),
            payment_terms=request.data.get('payment_terms', 'Auto-generated from quotation'),
            status='draft',
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            type='Fixed',
            description=quotation.title,
            qty=Decimal('1.00'),
            rate=quotation.total_amount,
            amount=quotation.total_amount,
        )
        invoice.total_amount = _calculate_total(invoice.subtotal, invoice.discount_total, invoice.tax_percentage)
        invoice.save(update_fields=['total_amount'])
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)

class InvoiceViewSet(viewsets.ModelViewSet):
    queryset = Invoice.objects.all()
    serializer_class = InvoiceSerializer
    required_permission = 'can_manage_billing'

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy', 'mark_as_paid']:
            return [permissions.IsAuthenticated(), HasTenantPermission()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Invoice.objects.all()
        role_name = user.role_obj.name if user.role_obj else ""
        if user.category == 'agency' and role_name in ['Owner', 'Agency Manager']:
            return Invoice.objects.all()
        # Clients can see their own invoices
        return Invoice.objects.filter(client=user)

    def _transition_project_tasks(self, invoice):
        """
        Transition associated project tasks to 'Done' once invoice is marked paid.
        """
        if invoice.project and invoice.status == 'paid':
            # Find the 'Done' task status in this project
            done_status = TaskStatus.objects.filter(project=invoice.project, name__iexact='Done').first()
            if done_status:
                # Find all billable tasks in this project and mark them Done
                tasks_to_transition = Task.objects.filter(project=invoice.project, is_billable=True)
                tasks_to_transition.update(status=done_status)

    def perform_create(self, serializer):
        invoice = serializer.save()
        self._transition_project_tasks(invoice)

    def perform_update(self, serializer):
        invoice = serializer.save()
        self._transition_project_tasks(invoice)

    @action(detail=True, methods=['post'])
    def mark_as_paid(self, request, pk=None):
        invoice = self.get_object()
        invoice.status = 'paid'
        invoice.save()
        self._transition_project_tasks(invoice)
        return Response({'status': 'invoice marked as paid and project tasks transitioned'})

    @action(detail=False, methods=['post'])
    def from_project(self, request):
        project_id = request.data.get('project_id')
        client_id = request.data.get('client_id')
        hourly_rate = Decimal(str(request.data.get('hourly_rate', '100.00')))
        include_estimates = bool(request.data.get('include_estimates', False))

        if not project_id or not client_id:
            return Response({'detail': 'project_id and client_id are required.'}, status=status.HTTP_400_BAD_REQUEST)

        project = Project.objects.filter(id=project_id).first()
        if not project:
            return Response({'detail': 'Project not found.'}, status=status.HTTP_404_NOT_FOUND)

        billable_tasks = Task.objects.filter(project=project, is_billable=True)
        line_items = []
        subtotal = Decimal('0.00')

        for task in billable_tasks:
            billed_minutes = task.time_entries.aggregate(total=Sum('duration_minutes')).get('total') or 0
            if billed_minutes > 0:
                hours = Decimal(billed_minutes) / Decimal('60')
                amount = (hours * hourly_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                line_items.append({
                    'type': 'Time',
                    'description': f'Time logged: {task.title}',
                    'qty': hours,
                    'rate': hourly_rate,
                    'amount': amount,
                })
                subtotal += amount
            elif include_estimates and task.estimated_hours > 0:
                amount = (Decimal(task.estimated_hours) * hourly_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                line_items.append({
                    'type': 'Time',
                    'description': f'Estimated effort: {task.title}',
                    'qty': task.estimated_hours,
                    'rate': hourly_rate,
                    'amount': amount,
                })
                subtotal += amount

        if not line_items:
            return Response({'detail': 'No billable task effort found for invoicing.'}, status=status.HTTP_400_BAD_REQUEST)

        issue_date = timezone.now().date()
        due_date = issue_date + timedelta(days=int(request.data.get('due_in_days', 14)))
        tax_percentage = Decimal(str(request.data.get('tax_percentage', '0.00')))
        discount_total = Decimal(str(request.data.get('discount_total', '0.00')))

        invoice = Invoice.objects.create(
            invoice_number=request.data.get('invoice_number') or _next_invoice_number(),
            client_id=client_id,
            project=project,
            issue_date=issue_date,
            due_date=due_date,
            subtotal=subtotal,
            tax_percentage=tax_percentage,
            discount_total=discount_total,
            total_amount=_calculate_total(subtotal, discount_total, tax_percentage),
            payment_terms=request.data.get('payment_terms', 'Auto-generated from billable project work'),
            status='draft',
        )
        for item in line_items:
            InvoiceLineItem.objects.create(invoice=invoice, **item)

        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def create_checkout_session(self, request, pk=None):
        invoice = self.get_object()
        success_url = request.data.get('success_url', 'http://localhost:5173/client-portal?payment=success')
        cancel_url = request.data.get('cancel_url', 'http://localhost:5173/client-portal?payment=cancelled')

        # The endpoint can run without the stripe package for local development.
        try:
            import stripe  # type: ignore
        except ImportError:
            return Response(
                {
                    'detail': 'Stripe SDK not installed. Install stripe package to enable real checkout.',
                    'checkout_url': f'{success_url}&invoice={invoice.id}',
                    'mode': 'mock'
                },
                status=status.HTTP_200_OK,
            )

        api_key = os.getenv('STRIPE_SECRET_KEY')
        if not api_key:
            return Response(
                {
                    'detail': 'Stripe key not configured. Using mock checkout mode.',
                    'checkout_url': f'{success_url}&invoice={invoice.id}',
                    'mode': 'mock'
                },
                status=status.HTTP_200_OK,
            )

        stripe.api_key = api_key
        session = stripe.checkout.Session.create(
            mode='payment',
            success_url=success_url,
            cancel_url=cancel_url,
            line_items=[
                {
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {'name': f'Invoice {invoice.invoice_number}'},
                        'unit_amount': int(invoice.total_amount * Decimal('100')),
                    },
                    'quantity': 1,
                }
            ],
            metadata={'invoice_id': str(invoice.id)},
        )
        return Response({'checkout_url': session.url, 'session_id': session.id}, status=status.HTTP_200_OK)

class RetainerAgreementViewSet(viewsets.ModelViewSet):
    queryset = RetainerAgreement.objects.all()
    serializer_class = RetainerAgreementSerializer
    required_permission = 'can_manage_billing'
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), HasTenantPermission()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return RetainerAgreement.objects.all()
        role_name = user.role_obj.name if user.role_obj else ""
        if user.category == 'agency' and role_name in ['Owner', 'Agency Manager']:
            return RetainerAgreement.objects.all()
        return RetainerAgreement.objects.filter(client=user)

    @action(detail=True, methods=['post'])
    def create_monthly_invoice(self, request, pk=None):
        retainer = self.get_object()
        issue_date = timezone.now().date()
        due_date = issue_date + timedelta(days=int(request.data.get('due_in_days', 7)))

        invoice = Invoice.objects.create(
            invoice_number=request.data.get('invoice_number') or _next_invoice_number(),
            client=retainer.client,
            issue_date=issue_date,
            due_date=due_date,
            subtotal=retainer.monthly_fee,
            tax_percentage=Decimal(str(request.data.get('tax_percentage', '0.00'))),
            discount_total=Decimal(str(request.data.get('discount_total', '0.00'))),
            payment_terms=request.data.get('payment_terms', 'Monthly retainer billing cycle'),
            status='sent' if request.data.get('auto_send', False) else 'draft',
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            type='Fixed',
            description=f'Monthly retainer ({retainer.included_hours}h included)',
            qty=Decimal('1.00'),
            rate=retainer.monthly_fee,
            amount=retainer.monthly_fee,
        )
        invoice.total_amount = _calculate_total(invoice.subtotal, invoice.discount_total, invoice.tax_percentage)
        invoice.save(update_fields=['total_amount'])
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)


class RecurringInvoiceScheduleViewSet(viewsets.ModelViewSet):
    queryset = RecurringInvoiceSchedule.objects.all()
    serializer_class = RecurringInvoiceScheduleSerializer
    required_permission = 'can_manage_billing'

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy', 'run_due']:
            return [permissions.IsAuthenticated(), HasTenantPermission()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return RecurringInvoiceSchedule.objects.all()
        role_name = user.role_obj.name if user.role_obj else ""
        if user.category == 'agency' and role_name in ['Owner', 'Agency Manager']:
            return RecurringInvoiceSchedule.objects.all()
        return RecurringInvoiceSchedule.objects.filter(client=user)

    @action(detail=False, methods=['post'])
    def run_due(self, request):
        today = timezone.now().date()
        due_schedules = self.get_queryset().filter(active=True, next_run_date__lte=today)
        generated = []

        for schedule in due_schedules:
            subtotal = schedule.amount
            invoice = Invoice.objects.create(
                invoice_number=_next_invoice_number(),
                client=schedule.client,
                project=schedule.project,
                issue_date=today,
                due_date=today + timedelta(days=7),
                subtotal=subtotal,
                tax_percentage=schedule.tax_percentage,
                discount_total=Decimal('0.00'),
                total_amount=_calculate_total(subtotal, Decimal('0.00'), schedule.tax_percentage),
                payment_terms='Auto-generated recurring invoice',
                status='sent' if schedule.auto_send else 'draft',
            )
            InvoiceLineItem.objects.create(
                invoice=invoice,
                type='Fixed',
                description=schedule.title,
                qty=Decimal('1.00'),
                rate=schedule.amount,
                amount=schedule.amount,
            )
            _bump_schedule_next_run(schedule)
            schedule.save(update_fields=['next_run_date', 'updated_at'])
            generated.append(str(invoice.id))

        return Response({'generated_invoice_ids': generated, 'count': len(generated)}, status=status.HTTP_200_OK)

class ExpenseViewSet(viewsets.ModelViewSet):
    queryset = Expense.objects.all()
    serializer_class = ExpenseSerializer
    required_permission = 'can_manage_billing'
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), HasTenantPermission()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Expense.objects.all()
        role_name = user.role_obj.name if user.role_obj else ""
        if user.category == 'agency' and role_name in ['Owner', 'Agency Manager']:
            return Expense.objects.all()
        # Expenses are typically internal, but if linked to a project that a user belongs to:
        return Expense.objects.filter(project__members__user=user).distinct()
