from rest_framework import serializers
from django.utils import timezone
from django.db.models import Sum
from decimal import Decimal
from .models import (
    Project, ProjectMember, ProjectRole, Task, TaskStatus, TaskAssignment,
    TaskChecklist, TaskCustomField, TaskUpdate, StorageConnection, FileRecord,
    NotificationLog, NotificationTemplate, IntegrationCredential, ReportSchedule, GeneratedReport,
    TimeEntry
)
from apps.authentication.serializers import UserSerializer
from apps.hr.models import LeaveRequest

class ProjectRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectRole
        fields = ['id', 'project', 'name', 'description', 'is_default']
        read_only_fields = ['id', 'is_default']

class ProjectMemberSerializer(serializers.ModelSerializer):
    user_details = UserSerializer(source='user', read_only=True)
    role_details = ProjectRoleSerializer(source='project_role', read_only=True)
    project_role_id = serializers.UUIDField(write_only=True, required=False)
    
    class Meta:
        model = ProjectMember
        fields = ['id', 'project', 'user', 'user_details', 'project_role', 'role_details', 'project_role_id', 'joined_at']
        read_only_fields = ['id', 'joined_at', 'project_role', 'role_details']

    def create(self, validated_data):
        project_role_id = validated_data.pop('project_role_id', None)
        if project_role_id:
            try:
                role = ProjectRole.objects.get(id=project_role_id)
                validated_data['project_role'] = role
            except ProjectRole.DoesNotExist:
                raise serializers.ValidationError({"project_role_id": "Project role does not exist."})
        return super().create(validated_data)

class ProjectSerializer(serializers.ModelSerializer):
    members = ProjectMemberSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = ['id', 'name', 'description', 'figma_embed_url', 'status', 'members', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class TaskStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskStatus
        fields = ['id', 'project', 'name', 'color', 'order']

class TimeEntrySerializer(serializers.ModelSerializer):
    user_details = UserSerializer(source='user', read_only=True)

    class Meta:
        model = TimeEntry
        fields = ['id', 'task', 'user', 'user_details', 'start_time', 'end_time', 'duration_minutes', 'is_locked', 'created_at']
        read_only_fields = ['id', 'created_at']


class TaskLiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = ['id', 'title', 'status', 'priority', 'deadline', 'kanban_position']

class TaskSerializer(serializers.ModelSerializer):
    status_details = TaskStatusSerializer(source='status', read_only=True)
    time_entries = TimeEntrySerializer(many=True, read_only=True)
    subtasks = TaskLiteSerializer(many=True, read_only=True)
    total_tracked_minutes = serializers.SerializerMethodField()
    timeline_conflicts = serializers.SerializerMethodField()
    
    class Meta:
        model = Task
        fields = [
            'id', 'project', 'parent_task', 'status', 'status_details',
            'title', 'description', 'estimated_hours', 'planned_start', 'deadline',
            'priority', 'is_billable', 'is_recurring', 'order', 'kanban_position',
            'subtasks', 'time_entries', 'total_tracked_minutes', 'timeline_conflicts',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_total_tracked_minutes(self, obj):
        return obj.time_entries.aggregate(total=Sum('duration_minutes')).get('total') or 0

    def _overlap_days(self, start_a, end_a, start_b, end_b):
        overlap_start = max(start_a, start_b)
        overlap_end = min(end_a, end_b)
        if overlap_start > overlap_end:
            return 0
        return (overlap_end - overlap_start).days + 1

    def get_timeline_conflicts(self, obj):
        warnings = []
        if not obj.deadline:
            return warnings

        now = timezone.now()
        start_dt = obj.planned_start or now
        end_dt = obj.deadline
        if end_dt <= now:
            warnings.append('Deadline has already passed.')
            return warnings

        if start_dt > end_dt:
            start_dt = now

        assignments = list(obj.assignments.select_related('user').all())
        assignees = [assignment.user for assignment in assignments]
        if not assignees:
            if obj.estimated_hours and obj.estimated_hours > 0:
                warnings.append('Task has estimated effort but no assignee.')
            return warnings

        per_user_estimate = Decimal(obj.estimated_hours or 0) / Decimal(len(assignees))

        for user in assignees:
            leave_days = 0
            approved_leaves = LeaveRequest.objects.filter(
                employee=user,
                status='approved',
                start_date__lte=end_dt.date(),
                end_date__gte=start_dt.date(),
            )
            for leave in approved_leaves:
                leave_days += self._overlap_days(start_dt.date(), end_dt.date(), leave.start_date, leave.end_date)

            total_days = (end_dt.date() - start_dt.date()).days + 1
            working_days = max(total_days - leave_days, 0)
            capacity_hours = Decimal(working_days) * Decimal(str(user.daily_working_hours))

            if leave_days > 0:
                warnings.append(f'{user.email} has {leave_days} approved leave day(s) during task window.')

            if per_user_estimate > capacity_hours:
                warnings.append(
                    f'{user.email} estimated load {per_user_estimate:.1f}h exceeds available capacity {capacity_hours:.1f}h before deadline.'
                )

        return warnings

class TaskAssignmentSerializer(serializers.ModelSerializer):
    user_details = UserSerializer(source='user', read_only=True)
    workload_warning = serializers.SerializerMethodField()

    class Meta:
        model = TaskAssignment
        fields = ['id', 'task', 'user', 'user_details', 'assigned_at', 'workload_warning']
        read_only_fields = ['id', 'assigned_at']

    def get_workload_warning(self, obj):
        user = obj.user
        task = obj.task
        
        if not task.deadline or not task.estimated_hours:
            return None
            
        now = timezone.now()
        time_left = task.deadline - now
        
        if time_left.total_seconds() <= 0:
            return "Deadline has already passed."
            
        # Basic calculation: purely based on days and user's daily working hours
        days_left = Decimal(time_left.days) + (Decimal(time_left.seconds) / Decimal(86400))
        available_hours = days_left * Decimal(str(user.daily_working_hours))
        
        if task.estimated_hours > available_hours:
            return f"Warning: Task requires {task.estimated_hours} hours, but user has only ~{available_hours:.1f} capacity hours before deadline."
            
        return None

class TaskChecklistSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskChecklist
        fields = ['id', 'task', 'title', 'is_completed']

class TaskCustomFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskCustomField
        fields = ['id', 'task', 'name', 'field_type', 'value']

class TaskUpdateSerializer(serializers.ModelSerializer):
    user_details = UserSerializer(source='user', read_only=True)
    approved_by_details = UserSerializer(source='approved_by', read_only=True)
    
    class Meta:
        model = TaskUpdate
        fields = [
            'id', 'task', 'user', 'user_details', 'content', 'is_deliverable', 'is_internal',
            'approval_status', 'approval_note', 'approved_by', 'approved_by_details', 'approved_at', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']

class StorageConnectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = StorageConnection
        fields = ['id', 'provider', 'connection_name', 'config', 'created_at']
        read_only_fields = ['id', 'created_at']

class FileRecordSerializer(serializers.ModelSerializer):
    uploaded_by_details = UserSerializer(source='uploaded_by', read_only=True)
    storage_connection_details = StorageConnectionSerializer(source='storage_connection', read_only=True)

    class Meta:
        model = FileRecord
        fields = [
            'id', 'project', 'storage_connection', 'storage_connection_details',
            'file_name', 'file_path', 'file_size', 'mime_type',
            'version', 'is_latest', 'previous_version', 'deleted_at',
            'uploaded_by', 'uploaded_by_details', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'uploaded_by', 'version', 'is_latest', 'previous_version', 'deleted_at']

class NotificationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationLog
        fields = ['id', 'user', 'title', 'message', 'is_read', 'created_at']
        read_only_fields = ['id', 'created_at']


class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = ['id', 'key', 'title_template', 'body_template', 'channel', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']

class IntegrationCredentialSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntegrationCredential
        fields = [
            'id', 'project', 'platform', 'client_id', 'client_secret',
            'access_token', 'refresh_token', 'properties', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']

class ReportScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportSchedule
        fields = ['id', 'project', 'name', 'frequency', 'recipients', 'layout', 'active', 'created_at']
        read_only_fields = ['id', 'created_at']

class GeneratedReportSerializer(serializers.ModelSerializer):
    schedule_details = ReportScheduleSerializer(source='schedule', read_only=True)

    class Meta:
        model = GeneratedReport
        fields = ['id', 'schedule', 'schedule_details', 'project', 'file_url', 'source_platforms', 'summary', 'created_at']
        read_only_fields = ['id', 'created_at']


class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = ['id', 'key', 'title_template', 'body_template', 'channel', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']

