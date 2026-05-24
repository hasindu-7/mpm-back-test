from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    QuotationViewSet, InvoiceViewSet, RetainerAgreementViewSet, RecurringInvoiceScheduleViewSet, ExpenseViewSet
)

router = DefaultRouter()
router.register('quotations', QuotationViewSet, basename='quotation')
router.register('invoices', InvoiceViewSet, basename='invoice')
router.register('retainers', RetainerAgreementViewSet, basename='retainer')
router.register('recurring-schedules', RecurringInvoiceScheduleViewSet, basename='recurring-schedule')
router.register('expenses', ExpenseViewSet, basename='expense')

urlpatterns = [
    path('', include(router.urls)),
]
