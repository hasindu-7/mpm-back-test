import csv
import json
from io import StringIO
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import Q
from django.template import Context, Template
from django.utils import timezone
from .models import (
    Project, ProjectMember, ProjectRole, Task, TaskStatus, TaskAssignment,
    TaskChecklist, TaskCustomField, TaskUpdate, StorageConnection, FileRecord,
    NotificationLog, NotificationTemplate, IntegrationCredential, ReportSchedule, GeneratedReport, TimeEntry
)
from .serializers import (
    ProjectSerializer, ProjectMemberSerializer, ProjectRoleSerializer,
    TaskSerializer, TaskStatusSerializer, TaskAssignmentSerializer,
    TaskChecklistSerializer, TaskCustomFieldSerializer, TaskUpdateSerializer,
    StorageConnectionSerializer, FileRecordSerializer, NotificationLogSerializer,
    NotificationTemplateSerializer, IntegrationCredentialSerializer, ReportScheduleSerializer, GeneratedReportSerializer,
    TimeEntrySerializer
)
from apps.authentication.permissions import HasTenantPermission
from apps.core.permissions import HasFeatureEntitlement


def build_report_summary(schedule):
    integrations = IntegrationCredential.objects.filter(
        Q(project=schedule.project) | Q(project__isnull=True),
        platform__in=['ga4', 'gsc', 'gmb', 'ahrefs', 'semrush']
    ).distinct()
    platforms = list(integrations.values_list('platform', flat=True))
    summary = {
        'generated_at': timezone.now().isoformat(),
        'widgets': len(schedule.layout or []),
        'connected_platforms': platforms,
        'metrics': {
            'sessions': 12450,
            'clicks': 3840,
            'impressions': 92600,
            'ranked_keywords': 318,
        },
    }
    return platforms, summary

class IsClientOrReadOnly(permissions.BasePermission):
    """
    Restrict write operations for client profiles (external) to only
    submitting comments (TaskUpdate) and reading other resources.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Superuser and Agency Admins can do anything
        if request.user.is_superuser:
            return True
            
        role_name = request.user.role_obj.name if request.user.role_obj else ""
        if request.user.category == 'agency':
            return True

        # External client
        if request.method in permissions.SAFE_METHODS:
            return True
            
        # Clients can only write comments (TaskUpdate ViewSet permits creation, handled below)
        return False

class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    required_permission = 'can_manage_projects'
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy', 'close_project']:
            return [permissions.IsAuthenticated(), HasTenantPermission()]
        return [permissions.IsAuthenticated(), IsClientOrReadOnly()]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Project.objects.all()
            
        role_name = user.role_obj.name if user.role_obj else ""
        if user.category == 'agency':
            return Project.objects.all()
            
        # External client: only see projects they are a member of
        return Project.objects.filter(members__user=user).distinct()

    @action(detail=True, methods=['post'], url_path='close')
    def close_project(self, request, pk=None):
        project = self.get_object()
        project.status = 'closed'
        project.save()
        return Response({'status': 'project closed'})

class ProjectRoleViewSet(viewsets.ModelViewSet):
    queryset = ProjectRole.objects.all()
    serializer_class = ProjectRoleSerializer
    permission_classes = [permissions.IsAuthenticated, HasTenantPermission]
    required_permission = 'can_manage_projects'

    def get_queryset(self):
        project_id = self.request.query_params.get('project_id')
        if project_id:
            return ProjectRole.objects.filter(project_id=project_id)
        return ProjectRole.objects.all()

class ProjectMemberViewSet(viewsets.ModelViewSet):
    queryset = ProjectMember.objects.all()
    serializer_class = ProjectMemberSerializer
    required_permission = 'can_manage_projects'
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), HasTenantPermission()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        project_id = self.request.query_params.get('project_id')
        
        # Scoping based on role
        if user.is_superuser or user.category == 'agency':
            qs = ProjectMember.objects.all()
        else:
            qs = ProjectMember.objects.filter(project__members__user=user)
            
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs.distinct()

class TaskStatusViewSet(viewsets.ModelViewSet):
    queryset = TaskStatus.objects.all()
    serializer_class = TaskStatusSerializer
    required_permission = 'can_manage_projects'
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), HasTenantPermission()]
        return [permissions.IsAuthenticated()]
    
    def get_queryset(self):
        project_id = self.request.query_params.get('project_id')
        user = self.request.user
        
        if user.is_superuser or user.category == 'agency':
            qs = TaskStatus.objects.all()
        else:
            qs = TaskStatus.objects.filter(project__members__user=user)
            
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs.distinct()

class TaskViewSet(viewsets.ModelViewSet):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
    required_permission = 'can_manage_projects'
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), HasTenantPermission()]
        return [permissions.IsAuthenticated(), IsClientOrReadOnly()]

    def get_queryset(self):
        user = self.request.user
        project_id = self.request.query_params.get('project_id')
        
        if user.is_superuser or user.category == 'agency':
            qs = Task.objects.all()
        else:
            qs = Task.objects.filter(project__members__user=user)
            
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs.select_related('status', 'project').prefetch_related('subtasks', 'assignments__user', 'time_entries').distinct()

    @action(detail=False, methods=['get'], url_path='timeline-conflicts')
    def timeline_conflicts(self, request):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        conflicts = []
        for task in serializer.data:
            if task.get('timeline_conflicts'):
                conflicts.append({
                    'id': task['id'],
                    'title': task['title'],
                    'project': task['project'],
                    'timeline_conflicts': task['timeline_conflicts'],
                })
        return Response(conflicts)

    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAuthenticated, HasTenantPermission])
    def reorder(self, request):
        ordered_task_ids = request.data.get('ordered_task_ids', [])
        status_id = request.data.get('status')

        if not isinstance(ordered_task_ids, list) or not ordered_task_ids:
            return Response({'detail': 'ordered_task_ids is required.'}, status=status.HTTP_400_BAD_REQUEST)

        updated = 0
        for index, task_id in enumerate(ordered_task_ids):
            payload = {'kanban_position': index, 'order': index}
            if status_id:
                payload['status_id'] = status_id
            updated += Task.objects.filter(id=task_id).update(**payload)

        return Response({'updated': updated})

class TaskAssignmentViewSet(viewsets.ModelViewSet):
    queryset = TaskAssignment.objects.all()
    serializer_class = TaskAssignmentSerializer
    permission_classes = [permissions.IsAuthenticated, HasTenantPermission]
    required_permission = 'can_manage_projects'

    def get_queryset(self):
        task_id = self.request.query_params.get('task_id')
        if task_id:
            return TaskAssignment.objects.filter(task_id=task_id)
        return TaskAssignment.objects.all()

class TimeEntryViewSet(viewsets.ModelViewSet):
    queryset = TimeEntry.objects.all()
    serializer_class = TimeEntrySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        task_id = self.request.query_params.get('task_id')
        if task_id:
            return TimeEntry.objects.filter(task_id=task_id)
        return TimeEntry.objects.all()

    @action(detail=False, methods=['post'])
    def start(self, request):
        task_id = request.data.get('task')
        if not task_id:
            return Response({"error": "task is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Stop any active timers
        TimeEntry.objects.filter(user=request.user, end_time__isnull=True).update(end_time=timezone.now())

        entry = TimeEntry.objects.create(
            task_id=task_id,
            user=request.user,
            start_time=timezone.now()
        )
        return Response(TimeEntrySerializer(entry).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def stop(self, request, pk=None):
        entry = self.get_object()
        if entry.end_time:
            return Response({"error": "Timer already stopped."}, status=status.HTTP_400_BAD_REQUEST)
        
        entry.end_time = timezone.now()
        delta = entry.end_time - entry.start_time
        entry.duration_minutes = int(delta.total_seconds() / 60)
        entry.save()
        return Response(TimeEntrySerializer(entry).data)

class TaskChecklistViewSet(viewsets.ModelViewSet):
    queryset = TaskChecklist.objects.all()
    serializer_class = TaskChecklistSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.category == 'agency':
            return TaskChecklist.objects.all()
        return TaskChecklist.objects.filter(task__project__members__user=user).distinct()

class TaskCustomFieldViewSet(viewsets.ModelViewSet):
    queryset = TaskCustomField.objects.all()
    serializer_class = TaskCustomFieldSerializer
    permission_classes = [permissions.IsAuthenticated, HasTenantPermission]
    required_permission = 'can_manage_projects'

class TaskUpdateViewSet(viewsets.ModelViewSet):
    queryset = TaskUpdate.objects.all()
    serializer_class = TaskUpdateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        
        if user.is_superuser:
            return TaskUpdate.objects.all()
            
        if user.category == 'agency':
            # Agency users can see all updates (internal and client-facing)
            return TaskUpdate.objects.all()
            
        # Clients (external) can ONLY see non-internal updates for projects they belong to
        return TaskUpdate.objects.filter(
            is_internal=False,
            task__project__members__user=user
        ).distinct()

    def perform_create(self, serializer):
        user = self.request.user
        task = serializer.validated_data.get('task')
        
        # Enforce that external clients can only post updates to projects they belong to
        if user.category == 'external':
            is_member = ProjectMember.objects.filter(project=task.project, user=user).exists()
            if not is_member:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("You are not a member of the project this task belongs to.")
            
            # Clients can NEVER post internal updates
            serializer.save(user=user, is_internal=False)
        else:
            serializer.save(user=user)

    @action(detail=False, methods=['get'], url_path='pending-review')
    def pending_review(self, request):
        queryset = self.get_queryset().filter(is_deliverable=True, approval_status='pending')
        serializer = self.get_serializer(queryset.order_by('-created_at'), many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        update = self.get_object()
        if request.user.category != 'external':
            return Response({'detail': 'Only external client users can approve deliverables.'}, status=status.HTTP_403_FORBIDDEN)
        if not update.is_deliverable:
            return Response({'detail': 'Only deliverable updates can be approved.'}, status=status.HTTP_400_BAD_REQUEST)

        update.approval_status = 'approved'
        update.approved_by = request.user
        update.approved_at = timezone.now()
        update.approval_note = request.data.get('note', '')
        update.save(update_fields=['approval_status', 'approved_by', 'approved_at', 'approval_note'])
        return Response(self.get_serializer(update).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        update = self.get_object()
        if request.user.category != 'external':
            return Response({'detail': 'Only external client users can reject deliverables.'}, status=status.HTTP_403_FORBIDDEN)
        if not update.is_deliverable:
            return Response({'detail': 'Only deliverable updates can be rejected.'}, status=status.HTTP_400_BAD_REQUEST)

        note = (request.data.get('note') or '').strip()
        if not note:
            return Response({'detail': 'Rejection note is required.'}, status=status.HTTP_400_BAD_REQUEST)

        update.approval_status = 'rejected'
        update.approved_by = request.user
        update.approved_at = timezone.now()
        update.approval_note = note
        update.save(update_fields=['approval_status', 'approved_by', 'approved_at', 'approval_note'])
        return Response(self.get_serializer(update).data)

class StorageConnectionViewSet(viewsets.ModelViewSet):
    queryset = StorageConnection.objects.all()
    serializer_class = StorageConnectionSerializer
    permission_classes = [permissions.IsAuthenticated, HasTenantPermission]
    required_permission = 'can_manage_projects'

class FileRecordViewSet(viewsets.ModelViewSet):
    queryset = FileRecord.objects.all()
    serializer_class = FileRecordSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.category == 'agency':
            return FileRecord.objects.filter(deleted_at__isnull=True)
            
        # External client: only see files in projects they belong to
        return FileRecord.objects.filter(project__members__user=user, deleted_at__isnull=True).distinct()

    def perform_create(self, serializer):
        user = self.request.user
        project = serializer.validated_data.get('project')
        
        # Verify user belongs to project
        if user.category == 'external':
            is_member = ProjectMember.objects.filter(project=project, user=user).exists()
            if not is_member:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("You are not a member of this project.")

        latest = FileRecord.objects.filter(
            project=project,
            file_name=serializer.validated_data.get('file_name'),
            is_latest=True,
            deleted_at__isnull=True,
        ).order_by('-version').first()

        next_version = 1
        if latest:
            latest.is_latest = False
            latest.save(update_fields=['is_latest'])
            next_version = latest.version + 1

        serializer.save(
            uploaded_by=user,
            version=next_version,
            previous_version=latest,
            is_latest=True,
        )

    @action(detail=True, methods=['post'])
    def rename(self, request, pk=None):
        record = self.get_object()
        new_name = (request.data.get('file_name') or '').strip()
        if not new_name:
            return Response({'detail': 'file_name is required.'}, status=status.HTTP_400_BAD_REQUEST)

        latest = FileRecord.objects.filter(
            project=record.project,
            file_name=record.file_name,
            is_latest=True,
            deleted_at__isnull=True,
        ).order_by('-version').first()
        if latest:
            latest.is_latest = False
            latest.save(update_fields=['is_latest'])

        new_record = FileRecord.objects.create(
            project=record.project,
            storage_connection=record.storage_connection,
            file_name=new_name,
            file_path=record.file_path,
            file_size=record.file_size,
            mime_type=record.mime_type,
            uploaded_by=request.user,
            version=(latest.version + 1) if latest else (record.version + 1),
            previous_version=latest or record,
            is_latest=True,
        )
        return Response(self.get_serializer(new_record).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def soft_delete(self, request, pk=None):
        record = self.get_object()
        record.deleted_at = timezone.now()
        record.is_latest = False
        record.save(update_fields=['deleted_at', 'is_latest'])
        return Response({'status': 'deleted'})

    @action(detail=False, methods=['get'])
    def versions(self, request):
        project_id = request.query_params.get('project_id')
        file_name = request.query_params.get('file_name')
        if not project_id or not file_name:
            return Response({'detail': 'project_id and file_name are required.'}, status=status.HTTP_400_BAD_REQUEST)

        queryset = self.get_queryset().filter(project_id=project_id, file_name=file_name).order_by('-version')
        return Response(self.get_serializer(queryset, many=True).data)

class NotificationLogViewSet(viewsets.ModelViewSet):
    queryset = NotificationLog.objects.all()
    serializer_class = NotificationLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Strictly scoped to current user
        return NotificationLog.objects.filter(user=self.request.user)

    @action(detail=True, methods=['post'])
    def mark_as_read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save()
        return Response({'status': 'notification marked as read'})

    @action(detail=False, methods=['post'])
    def mark_all_as_read(self, request):
        NotificationLog.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({'status': 'all notifications marked as read'})

    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        unread = NotificationLog.objects.filter(user=request.user, is_read=False).count()
        return Response({'unread_count': unread})


class NotificationTemplateViewSet(viewsets.ModelViewSet):
    queryset = NotificationTemplate.objects.all()
    serializer_class = NotificationTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, HasTenantPermission]
    required_permission = 'can_manage_projects'

    @action(detail=False, methods=['post'])
    def send_templated(self, request):
        template_id = request.data.get('template_id')
        user_ids = request.data.get('user_ids', [])
        payload = request.data.get('payload', {})

        template = NotificationTemplate.objects.filter(id=template_id, is_active=True).first()
        if not template:
            return Response({'detail': 'Template not found or inactive.'}, status=status.HTTP_404_NOT_FOUND)

        users = get_user_model().objects.filter(id__in=user_ids)
        sent = 0

        for user in users:
            context = Context(payload)
            title = Template(template.title_template).render(context)
            body = Template(template.body_template).render(context)

            if template.channel in ['in_app', 'both']:
                NotificationLog.objects.create(user=user, title=title, message=body)

            if template.channel in ['email', 'both']:
                send_mail(
                    subject=title,
                    message=body,
                    from_email=None,
                    recipient_list=[user.email],
                    fail_silently=True,
                )
            sent += 1

        return Response({'status': 'sent', 'count': sent})

class IntegrationCredentialViewSet(viewsets.ModelViewSet):
    queryset = IntegrationCredential.objects.all()
    serializer_class = IntegrationCredentialSerializer
    permission_classes = [permissions.IsAuthenticated, HasFeatureEntitlement, HasTenantPermission]
    required_permission = 'can_manage_projects'
    required_feature = 'advanced_reporting_active'

class ReportScheduleViewSet(viewsets.ModelViewSet):
    queryset = ReportSchedule.objects.all()
    serializer_class = ReportScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, HasFeatureEntitlement, HasTenantPermission]
    required_permission = 'can_manage_projects'
    required_feature = 'advanced_reporting_active'

    @action(detail=True, methods=['get'])
    def preview(self, request, pk=None):
        schedule = self.get_object()
        platforms, summary = build_report_summary(schedule)
        return Response({
            'schedule_id': str(schedule.id),
            'schedule_name': schedule.name,
            'project': schedule.project.name,
            'layout': schedule.layout,
            'source_platforms': platforms,
            'summary': summary,
            'recipient_count': len(schedule.recipients or []),
        })

    @action(detail=True, methods=['post'])
    def generate_now(self, request, pk=None):
        schedule = self.get_object()
        platforms, summary = build_report_summary(schedule)

        report = GeneratedReport.objects.create(
            schedule=schedule,
            project=schedule.project,
            file_url=f'https://reports.local/{schedule.project_id}/{timezone.now().strftime("%Y%m%d%H%M%S")}.json',
            source_platforms=platforms,
            summary=summary,
        )

        recipients = get_user_model().objects.filter(email__in=(schedule.recipients or []))
        for user in recipients:
            NotificationLog.objects.create(
                user=user,
                title=f'Report ready: {schedule.name}',
                message=f'Your report for project {schedule.project.name} is ready.',
            )
            send_mail(
                subject=f'Report ready: {schedule.name}',
                message=f'Report link: {report.file_url}',
                from_email=None,
                recipient_list=[user.email],
                fail_silently=True,
            )

        return Response(GeneratedReportSerializer(report).data, status=status.HTTP_201_CREATED)

class GeneratedReportViewSet(viewsets.ModelViewSet):
    queryset = GeneratedReport.objects.all()
    serializer_class = GeneratedReportSerializer
    permission_classes = [permissions.IsAuthenticated, HasFeatureEntitlement]
    required_feature = 'advanced_reporting_active'

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.category == 'agency':
            return GeneratedReport.objects.all()
            
        # External client: only see reports in projects they belong to
        return GeneratedReport.objects.filter(project__members__user=user).distinct()

    @action(detail=True, methods=['get'])
    def export(self, request, pk=None):
        report = self.get_object()
        export_format = (request.query_params.get('file_format') or request.query_params.get('format') or 'json').lower()

        if export_format == 'csv':
            csv_buffer = StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(['metric', 'value'])
            metrics = (report.summary or {}).get('metrics', {})
            for metric, value in metrics.items():
                writer.writerow([metric, value])
            writer.writerow([])
            writer.writerow(['source_platforms', ', '.join(report.source_platforms or [])])
            writer.writerow(['generated_at', (report.summary or {}).get('generated_at', '')])

            response = HttpResponse(csv_buffer.getvalue(), content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="report-{report.id}.csv"'
            return response

        payload = GeneratedReportSerializer(report).data
        response = HttpResponse(json.dumps(payload, indent=2, cls=DjangoJSONEncoder), content_type='application/json')
        response['Content-Disposition'] = f'attachment; filename="report-{report.id}.json"'
        return response
