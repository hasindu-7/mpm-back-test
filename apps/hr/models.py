import uuid
from django.db import models
from django.conf import settings

class EmployeeProfile(models.Model):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('terminated', 'Terminated'),
    )
    PAY_FREQUENCY_CHOICES = (
        ('monthly', 'Monthly'),
        ('biweekly', 'Bi-Weekly'),
        ('weekly', 'Weekly'),
        ('hourly', 'Hourly'),
    )
    EMPLOYMENT_TYPE_CHOICES = (
        ('full_time', 'Full Time'),
        ('part_time', 'Part Time'),
        ('contract', 'Contract'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='employee_profile')
    department = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=100, blank=True)
    salary = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    pay_frequency = models.CharField(max_length=20, choices=PAY_FREQUENCY_CHOICES, default='monthly')
    employment_type = models.CharField(max_length=20, choices=EMPLOYMENT_TYPE_CHOICES, default='full_time')
    phone = models.CharField(max_length=30, blank=True)
    emergency_contact = models.CharField(max_length=120, blank=True)
    start_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} - {self.title or 'Employee'}"

class LeaveType(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    annual_allocation = models.PositiveIntegerField(default=14) # in days
    carryover_rules = models.TextField(blank=True) # rules for carry-over to next year

    def __str__(self):
        return self.name

class LeaveRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='requests')
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reason = models.TextField(blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_leaves')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.employee.email} - {self.leave_type.name} ({self.start_date} to {self.end_date})"

class PayrollRun(models.Model):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('approved', 'Approved'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pay_period = models.CharField(max_length=7) # format: YYYY-MM e.g. 2026-05
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Payroll Run - {self.pay_period} ({self.status})"

class Payslip(models.Model):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('issued', 'Issued'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name='payslips')
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payslips')
    base_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    net_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Payslip for {self.employee.email} - Period: {self.payroll_run.pay_period}"
