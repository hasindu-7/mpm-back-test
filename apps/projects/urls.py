from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ProjectViewSet, ProjectMemberViewSet, ProjectRoleViewSet,
    TaskViewSet, TaskStatusViewSet, TaskAssignmentViewSet,
    TimeEntryViewSet,
    TaskChecklistViewSet, TaskCustomFieldViewSet, TaskUpdateViewSet,
    StorageConnectionViewSet, FileRecordViewSet, NotificationLogViewSet,
    NotificationTemplateViewSet, IntegrationCredentialViewSet, ReportScheduleViewSet, GeneratedReportViewSet
)

router = DefaultRouter()
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'project-roles', ProjectRoleViewSet, basename='project-role')
router.register(r'members', ProjectMemberViewSet, basename='project-member')
router.register(r'task-statuses', TaskStatusViewSet, basename='task-status')
router.register(r'tasks', TaskViewSet, basename='task')
router.register(r'assignments', TaskAssignmentViewSet, basename='task-assignment')
router.register(r'time-entries', TimeEntryViewSet, basename='time-entry')
router.register(r'checklists', TaskChecklistViewSet, basename='task-checklist')
router.register(r'custom-fields', TaskCustomFieldViewSet, basename='task-custom-field')
router.register(r'updates', TaskUpdateViewSet, basename='task-update')
router.register(r'storage-connections', StorageConnectionViewSet, basename='storage-connection')
router.register(r'file-records', FileRecordViewSet, basename='file-record')
router.register(r'notification-logs', NotificationLogViewSet, basename='notification-log')
router.register(r'notification-templates', NotificationTemplateViewSet, basename='notification-template')
router.register(r'integration-credentials', IntegrationCredentialViewSet, basename='integration-credential')
router.register(r'report-schedules', ReportScheduleViewSet, basename='report-schedule')
router.register(r'generated-reports', GeneratedReportViewSet, basename='generated-report')

urlpatterns = [
    path('', include(router.urls)),
]


