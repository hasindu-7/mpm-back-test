from rest_framework import serializers
from django.utils import timezone
from django.contrib.auth import get_user_model
from .models import EmployeeProfile, LeaveType, LeaveRequest, PayrollRun, Payslip
from apps.authentication.serializers import UserSerializer

User = get_user_model()

class EmployeeProfileSerializer(serializers.ModelSerializer):
    user_details = UserSerializer(source='user', read_only=True)
    user_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = EmployeeProfile
        fields = [
            'id', 'user', 'user_id', 'user_details', 'department', 'title',
            'salary', 'pay_frequency', 'employment_type', 'phone', 'emergency_contact',
            'start_date', 'status', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']

    def create(self, validated_data):
        user_id = validated_data.pop('user_id')
        try:
            user = User.objects.get(id=user_id)
            validated_data['user'] = user
        except User.DoesNotExist:
            raise serializers.ValidationError({"user_id": "User does not exist."})
        return super().create(validated_data)

class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = ['id', 'name', 'annual_allocation', 'carryover_rules']

class LeaveRequestSerializer(serializers.ModelSerializer):
    employee_details = UserSerializer(source='employee', read_only=True)
    leave_type_details = LeaveTypeSerializer(source='leave_type', read_only=True)
    approved_by_details = UserSerializer(source='approved_by', read_only=True)

    class Meta:
        model = LeaveRequest
        fields = [
            'id', 'employee', 'employee_details', 'leave_type', 'leave_type_details',
            'start_date', 'end_date', 'status', 'reason', 'approved_by', 'approved_by_details',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'approved_by', 'created_at', 'updated_at']

    def validate(self, attrs):
        start_date = attrs.get('start_date')
        end_date = attrs.get('end_date')
        employee = attrs.get('employee') or self.context['request'].user
        leave_type = attrs.get('leave_type')

        if start_date and end_date:
            if start_date > end_date:
                raise serializers.ValidationError({"start_date": "Start date must be before or equal to end date."})
            
            # Calculate duration in days
            requested_days = (end_date - start_date).days + 1
            
            # Determine annual limit
            annual_limit = leave_type.annual_allocation
            
            # Get already approved leave days in this year
            current_year = start_date.year
            approved_requests = LeaveRequest.objects.filter(
                employee=employee,
                leave_type=leave_type,
                status='approved',
                start_date__year=current_year
            )
            
            used_days = 0
            for req in approved_requests:
                used_days += (req.end_date - req.start_date).days + 1
            
            # If editing, don't count the current record
            if self.instance and self.instance.status == 'approved' and self.instance.leave_type == leave_type:
                used_days -= (self.instance.end_date - self.instance.start_date).days + 1

            if used_days + requested_days > annual_limit:
                raise serializers.ValidationError({
                    "non_field_errors": f"Leave balance exceeded. You have {annual_limit - used_days} days left, but requested {requested_days} days."
                })

        return attrs

class PayrollRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollRun
        fields = ['id', 'pay_period', 'status', 'created_at', 'updated_at']

class PayslipSerializer(serializers.ModelSerializer):
    employee_details = UserSerializer(source='employee', read_only=True)
    payroll_run_details = PayrollRunSerializer(source='payroll_run', read_only=True)

    class Meta:
        model = Payslip
        fields = [
            'id', 'payroll_run', 'payroll_run_details', 'employee', 'employee_details',
            'base_salary', 'net_pay', 'status', 'created_at', 'updated_at'
        ]
