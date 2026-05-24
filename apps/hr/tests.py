from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from datetime import date
from apps.hr.models import LeaveType, LeaveRequest, EmployeeProfile, PayrollRun, Payslip
from apps.hr.serializers import LeaveRequestSerializer

User = get_user_model()

class HRLeaveRequestTests(TestCase):
    def setUp(self):
        self.employee = User.objects.create_user(
            email='employee@agency.com',
            password='testpassword123',
            category='agency'
        )
        self.leave_type = LeaveType.objects.create(
            name="Annual Leave",
            annual_allocation=10
        )

    def test_leave_request_serializer_validation(self):
        # 1. Requesting 5 days should succeed (within 10 days allocation)
        data = {
            'employee': self.employee.id,
            'leave_type': self.leave_type.id,
            'start_date': date(2026, 6, 1),
            'end_date': date(2026, 6, 5), # 5 days inclusive
            'reason': 'Vacation'
        }
        serializer = LeaveRequestSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        
        # Save and mark as approved to exhaust leave allocation
        leave_request = serializer.save()
        leave_request.status = 'approved'
        leave_request.save()
        
        # 2. Requesting another 6 days should fail (total 11 days, limit is 10)
        data_exceed = {
            'employee': self.employee.id,
            'leave_type': self.leave_type.id,
            'start_date': date(2026, 6, 10),
            'end_date': date(2026, 6, 15), # 6 days inclusive
            'reason': 'More Vacation'
        }
        serializer_exceed = LeaveRequestSerializer(data=data_exceed)
        self.assertFalse(serializer_exceed.is_valid())
        self.assertIn('non_field_errors', serializer_exceed.errors)
        self.assertTrue("Leave balance exceeded" in serializer_exceed.errors['non_field_errors'][0])


class HRWorkflowAPITests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.owner = User.objects.create_user(
            email='owner@agency.com',
            password='Password123!',
            category='agency',
            is_superuser=True,
        )
        self.employee = User.objects.create_user(
            email='employee2@agency.com',
            password='Password123!',
            category='agency',
        )
        self.leave_type = LeaveType.objects.create(name='Sick Leave', annual_allocation=10)
        self.leave = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            start_date=date(2026, 5, 24),
            end_date=date(2026, 5, 25),
            status='pending',
        )

    def test_non_approver_cannot_approve_leave(self):
        self.client_api.force_authenticate(user=self.employee)
        response = self.client_api.post(f'/api/leave-requests/{self.leave.id}/approve/', {}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_approver_can_approve_and_calendar_lists_event(self):
        self.client_api.force_authenticate(user=self.owner)
        approve_response = self.client_api.post(f'/api/leave-requests/{self.leave.id}/approve/', {}, format='json')
        self.assertEqual(approve_response.status_code, 200)

        calendar_response = self.client_api.get('/api/leave-requests/calendar/?start=2026-05-01&end=2026-05-31')
        self.assertEqual(calendar_response.status_code, 200)
        self.assertGreaterEqual(calendar_response.data['count'], 1)

    def test_payslip_pdf_download(self):
        EmployeeProfile.objects.create(user=self.employee, title='Developer', salary='3000.00')
        run = PayrollRun.objects.create(pay_period='2026-05', status='draft')
        payslip = Payslip.objects.create(
            payroll_run=run,
            employee=self.employee,
            base_salary='3000.00',
            net_pay='3000.00',
            status='issued',
        )

        self.client_api.force_authenticate(user=self.owner)
        response = self.client_api.get(f'/api/payslips/{payslip.id}/download_pdf/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF-1.4'))
