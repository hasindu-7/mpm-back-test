from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import HttpResponse
from django.utils import timezone
from datetime import datetime
from .models import EmployeeProfile, LeaveType, LeaveRequest, PayrollRun, Payslip
from .serializers import (
    EmployeeProfileSerializer, LeaveTypeSerializer, LeaveRequestSerializer,
    PayrollRunSerializer, PayslipSerializer
)
from apps.authentication.permissions import HasTenantPermission
from apps.core.permissions import HasFeatureEntitlement


def _build_simple_pdf(lines):
    content = "\n".join(lines)
    stream = f"BT /F1 11 Tf 50 760 Td ({content.replace('(', '[').replace(')', ']')}) Tj ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj",
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        f"5 0 obj << /Length {len(stream)} >> stream\n{stream}\nendstream endobj",
    ]

    pdf = "%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf.encode('utf-8')))
        pdf += obj + "\n"
    xref_pos = len(pdf.encode('utf-8'))
    pdf += f"xref\n0 {len(objects) + 1}\n"
    pdf += "0000000000 65535 f \n"
    for off in offsets[1:]:
        pdf += f"{off:010d} 00000 n \n"
    pdf += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF"
    return pdf.encode('utf-8')


def _is_hr_approver(user):
    if user.is_superuser:
        return True
    role_name = user.role_obj.name if user.role_obj else ""
    return user.category == 'agency' and role_name in ['Owner', 'Agency Manager']

class EmployeeProfileViewSet(viewsets.ModelViewSet):
    queryset = EmployeeProfile.objects.all()
    serializer_class = EmployeeProfileSerializer
    required_permission = 'can_manage_hr'
    required_feature = 'hr_leave_active'
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), HasFeatureEntitlement(), HasTenantPermission()]
        return [permissions.IsAuthenticated(), HasFeatureEntitlement()]

class LeaveTypeViewSet(viewsets.ModelViewSet):
    queryset = LeaveType.objects.all()
    serializer_class = LeaveTypeSerializer
    required_permission = 'can_manage_hr'
    required_feature = 'hr_leave_active'
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), HasFeatureEntitlement(), HasTenantPermission()]
        return [permissions.IsAuthenticated(), HasFeatureEntitlement()]

class LeaveRequestViewSet(viewsets.ModelViewSet):
    queryset = LeaveRequest.objects.all()
    serializer_class = LeaveRequestSerializer
    permission_classes = [permissions.IsAuthenticated, HasFeatureEntitlement]
    required_permission = 'can_manage_hr'
    required_feature = 'hr_leave_active'

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return LeaveRequest.objects.all()
        # Agency Admin (Owner/Manager) can see all requests.
        role_name = user.role_obj.name if user.role_obj else ""
        if user.category == 'agency' and role_name in ['Owner', 'Agency Manager']:
            return LeaveRequest.objects.all()
        # Regular employees see only their own requests.
        return LeaveRequest.objects.filter(employee=user)

    def perform_create(self, serializer):
        # Default employee to the requesting user unless they are admin and specify another
        user = self.request.user
        employee = serializer.validated_data.get('employee', user)
        serializer.save(employee=employee)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated, HasTenantPermission])
    def approve(self, request, pk=None):
        if not _is_hr_approver(request.user):
            return Response({'detail': 'Only agency managers/owners can approve leave.'}, status=status.HTTP_403_FORBIDDEN)
        leave = self.get_object()
        leave.status = 'approved'
        leave.approved_by = request.user
        leave.save()
        return Response({'status': 'leave request approved'})

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated, HasTenantPermission])
    def reject(self, request, pk=None):
        if not _is_hr_approver(request.user):
            return Response({'detail': 'Only agency managers/owners can reject leave.'}, status=status.HTTP_403_FORBIDDEN)
        leave = self.get_object()
        leave.status = 'rejected'
        leave.approved_by = request.user
        leave.save()
        return Response({'status': 'leave request rejected'})

    @action(detail=False, methods=['get'])
    def calendar(self, request):
        start_raw = request.query_params.get('start')
        end_raw = request.query_params.get('end')
        queryset = self.get_queryset().select_related('employee', 'leave_type')

        if start_raw:
            start_date = datetime.strptime(start_raw, '%Y-%m-%d').date()
            queryset = queryset.filter(end_date__gte=start_date)
        if end_raw:
            end_date = datetime.strptime(end_raw, '%Y-%m-%d').date()
            queryset = queryset.filter(start_date__lte=end_date)

        events = [
            {
                'id': str(req.id),
                'title': f"{req.employee.email} - {req.leave_type.name}",
                'start': req.start_date.isoformat(),
                'end': req.end_date.isoformat(),
                'status': req.status,
            }
            for req in queryset
        ]
        return Response({'events': events, 'count': len(events)})

class PayrollRunViewSet(viewsets.ModelViewSet):
    queryset = PayrollRun.objects.all()
    serializer_class = PayrollRunSerializer
    permission_classes = [permissions.IsAuthenticated, HasFeatureEntitlement, HasTenantPermission]
    required_permission = 'can_manage_hr'
    required_feature = 'hr_leave_active'

    @action(detail=True, methods=['post'])
    def generate_payslips(self, request, pk=None):
        payroll_run = self.get_object()
        active_employees = EmployeeProfile.objects.filter(status='active')
        
        created_count = 0
        for emp in active_employees:
            # Check if payslip already exists for this employee in this run
            if not Payslip.objects.filter(payroll_run=payroll_run, employee=emp.user).exists():
                Payslip.objects.create(
                    payroll_run=payroll_run,
                    employee=emp.user,
                    base_salary=emp.salary,
                    net_pay=emp.salary, # Net pay matches base by default, can be modified
                    status='draft'
                )
                created_count += 1
                
        return Response({
            'status': 'payslips generated',
            'created_count': created_count
        })

    @action(detail=True, methods=['post'])
    def approve_payroll(self, request, pk=None):
        payroll_run = self.get_object()
        payroll_run.status = 'approved'
        payroll_run.save()
        
        # Also mark all payslips in this run as issued
        Payslip.objects.filter(payroll_run=payroll_run).update(status='issued')
        
        return Response({'status': 'payroll run approved and payslips issued'})

class PayslipViewSet(viewsets.ModelViewSet):
    queryset = Payslip.objects.all()
    serializer_class = PayslipSerializer
    required_permission = 'can_manage_hr'
    required_feature = 'hr_leave_active'
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), HasFeatureEntitlement(), HasTenantPermission()]
        return [permissions.IsAuthenticated(), HasFeatureEntitlement()]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Payslip.objects.all()
        
        role_name = user.role_obj.name if user.role_obj else ""
        if user.category == 'agency' and role_name in ['Owner', 'Agency Manager']:
            return Payslip.objects.all()
            
        return Payslip.objects.filter(employee=user)

    @action(detail=True, methods=['get'])
    def download_pdf(self, request, pk=None):
        payslip = self.get_object()
        lines = [
            f"Payslip: {payslip.id}",
            f"Employee: {payslip.employee.email}",
            f"Period: {payslip.payroll_run.pay_period}",
            f"Base Salary: {payslip.base_salary}",
            f"Net Pay: {payslip.net_pay}",
            f"Status: {payslip.status}",
        ]
        pdf_bytes = _build_simple_pdf(lines)
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="payslip-{payslip.payroll_run.pay_period}.pdf"'
        return response
