from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    EmployeeProfileViewSet, LeaveTypeViewSet, LeaveRequestViewSet,
    PayrollRunViewSet, PayslipViewSet
)

router = DefaultRouter()
router.register('employees', EmployeeProfileViewSet, basename='employee')
router.register('leave-types', LeaveTypeViewSet, basename='leave-type')
router.register('leave-requests', LeaveRequestViewSet, basename='leave-request')
router.register('payroll-runs', PayrollRunViewSet, basename='payroll-run')
router.register('payslips', PayslipViewSet, basename='payslip')

urlpatterns = [
    path('', include(router.urls)),
]
