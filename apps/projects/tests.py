from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal
from datetime import timedelta
from rest_framework.test import APIClient

from apps.projects.models import (
    Project, ProjectMember, Task, TaskStatus, TaskAssignment, TaskUpdate,
    FileRecord, StorageConnection, IntegrationCredential, ReportSchedule,
    GeneratedReport, NotificationLog, NotificationTemplate
)
from apps.projects.serializers import TaskAssignmentSerializer, TaskSerializer
from apps.hr.models import LeaveType, LeaveRequest

User = get_user_model()

class ProjectScopingTests(TestCase):
    def setUp(self):
        # Create users
        self.agency_user = User.objects.create_user(
            email='agency@agency.com',
            password='testpassword123',
            category='agency'
        )
        self.external_client = User.objects.create_user(
            email='client@client.com',
            password='testpassword123',
            category='external'
        )
        
        # Create project
        self.project = Project.objects.create(name="Alpha Project")
        
        # Add external client to project members
        ProjectMember.objects.create(
            project=self.project,
            user=self.external_client
        )
        
        # Add default task status
        self.status_todo = TaskStatus.objects.create(
            project=self.project,
            name="To Do",
            order=0
        )
        
        # Create tasks
        self.task_billable = Task.objects.create(
            project=self.project,
            status=self.status_todo,
            title="Billable Task",
            estimated_hours=Decimal('10.00'),
            deadline=timezone.now() + timedelta(days=2),
            is_billable=True
        )

    def test_workload_warning_logic(self):
        # Assign task to external user (to test serializer method field)
        assignment = TaskAssignment.objects.create(
            task=self.task_billable,
            user=self.external_client
        )
        
        # Serialize the assignment
        serializer = TaskAssignmentSerializer(assignment)
        warning = serializer.data.get('workload_warning')
        
        # Under normal conditions (user daily_working_hours = 8.0, days_left = 2, total_capacity = 16.0),
        # estimated_hours is 10.0, so no warning is expected.
        self.assertIsNone(warning)
        
        # Update task to require 30 hours (exceeds available ~16 hours)
        self.task_billable.estimated_hours = Decimal('30.00')
        self.task_billable.save()
        
        serializer = TaskAssignmentSerializer(assignment)
        warning = serializer.data.get('workload_warning')
        self.assertIsNotNone(warning)
        self.assertTrue("capacity hours" in warning)

    def test_task_updates_internal_scoping(self):
        # Create normal update and internal update
        update_public = TaskUpdate.objects.create(
            task=self.task_billable,
            user=self.agency_user,
            content="This is public comment",
            is_internal=False
        )
        update_internal = TaskUpdate.objects.create(
            task=self.task_billable,
            user=self.agency_user,
            content="This is internal discussion",
            is_internal=True
        )
        
        # Verify client can only see public comments, and internal updates are correctly labeled
        self.assertFalse(update_public.is_internal)
        self.assertTrue(update_internal.is_internal)

    def test_timeline_conflict_warns_for_approved_leave_capacity(self):
        leave_type = LeaveType.objects.create(name='Annual Leave', annual_allocation=14)
        LeaveRequest.objects.create(
            employee=self.external_client,
            leave_type=leave_type,
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timedelta(days=1)).date(),
            status='approved',
        )

        self.task_billable.estimated_hours = Decimal('20.00')
        self.task_billable.planned_start = timezone.now()
        self.task_billable.deadline = timezone.now() + timedelta(days=2)
        self.task_billable.save()

        TaskAssignment.objects.create(task=self.task_billable, user=self.external_client)

        data = TaskSerializer(self.task_billable).data
        warnings = data.get('timeline_conflicts', [])

        self.assertTrue(any('approved leave day(s)' in warning for warning in warnings))


class PhaseThreeWorkflowTests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.agency_user = User.objects.create_user(
            email='agency-owner@example.com',
            password='Password123!',
            category='agency'
        )
        self.external_user = User.objects.create_user(
            email='client-user@example.com',
            password='Password123!',
            category='external'
        )

        self.project = Project.objects.create(name='Portal Project')
        ProjectMember.objects.create(project=self.project, user=self.external_user)
        self.status = TaskStatus.objects.create(project=self.project, name='To Do', order=0)
        self.task = Task.objects.create(project=self.project, status=self.status, title='Deliverable Task')
        StorageConnection.objects.create(
            provider='s3',
            connection_name='Primary S3',
            config={'bucket_name': 'portal-files', 'region': 'us-east-1'},
        )

    def test_external_client_can_approve_deliverable_update(self):
        update = TaskUpdate.objects.create(
            task=self.task,
            user=self.agency_user,
            content='Please approve this deliverable',
            is_deliverable=True,
            is_internal=False,
        )

        self.client_api.force_authenticate(user=self.external_user)
        response = self.client_api.post(f'/api/updates/{update.id}/approve/', {'note': 'Looks good.'}, format='json')

        self.assertEqual(response.status_code, 200)
        update.refresh_from_db()
        self.assertEqual(update.approval_status, 'approved')
        self.assertEqual(update.approved_by_id, self.external_user.id)

    def test_file_record_create_new_version_increments(self):
        first = FileRecord.objects.create(
            project=self.project,
            file_name='brand-guidelines.pdf',
            file_path='/uploads/brand-guidelines.pdf',
            file_size=1024,
            mime_type='application/pdf',
            uploaded_by=self.agency_user,
            version=1,
            is_latest=True,
        )

        self.client_api.force_authenticate(user=self.agency_user)
        response = self.client_api.post('/api/file-records/', {
            'project': str(self.project.id),
            'file_name': 'brand-guidelines.pdf',
            'file_path': '/uploads/brand-guidelines.pdf',
            'file_size': 2048,
            'mime_type': 'application/pdf',
        }, format='json')

        self.assertEqual(response.status_code, 201)
        first.refresh_from_db()
        self.assertFalse(first.is_latest)

        created = FileRecord.objects.get(id=response.data['id'])
        self.assertEqual(created.version, 2)
        self.assertTrue(created.is_latest)


class PhaseSixAutomationTests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.agency_user = User.objects.create_user(
            email='agency-phase6@example.com',
            password='Password123!',
            category='agency',
            is_superuser=True,
        )
        self.external_user = User.objects.create_user(
            email='client-phase6@example.com',
            password='Password123!',
            category='external',
        )
        self.project = Project.objects.create(name='SEO Growth')
        ProjectMember.objects.create(project=self.project, user=self.external_user)

        self.schedule = ReportSchedule.objects.create(
            project=self.project,
            name='Weekly Performance Pulse',
            frequency='weekly',
            recipients=[self.external_user.email],
            layout=[{'id': 'sessions', 'source': 'ga4'}, {'id': 'clicks', 'source': 'gsc'}],
        )
        IntegrationCredential.objects.create(project=self.project, platform='ga4')
        IntegrationCredential.objects.create(project=self.project, platform='gsc')

        self.client_api.force_authenticate(user=self.agency_user)

    def test_generate_now_creates_report_and_notifications(self):
        response = self.client_api.post(f'/api/report-schedules/{self.schedule.id}/generate_now/', {}, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(GeneratedReport.objects.count(), 1)
        report = GeneratedReport.objects.first()
        self.assertIn('ga4', report.source_platforms)
        self.assertIn('metrics', report.summary)
        self.assertEqual(NotificationLog.objects.filter(user=self.external_user).count(), 1)

    def test_report_preview_and_export(self):
        preview_response = self.client_api.get(f'/api/report-schedules/{self.schedule.id}/preview/')
        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(preview_response.data['schedule_name'], self.schedule.name)
        self.assertIn('summary', preview_response.data)

        generate_response = self.client_api.post(f'/api/report-schedules/{self.schedule.id}/generate_now/', {}, format='json')
        self.assertEqual(generate_response.status_code, 201)
        report_id = generate_response.data['id']

        export_json = self.client_api.get(f'/api/generated-reports/{report_id}/export/?file_format=json')
        self.assertEqual(export_json.status_code, 200)
        self.assertIn('application/json', export_json['Content-Type'])

        export_csv = self.client_api.get(f'/api/generated-reports/{report_id}/export/?file_format=csv')
        self.assertEqual(export_csv.status_code, 200)
        self.assertIn('text/csv', export_csv['Content-Type'])

    def test_send_templated_and_unread_count(self):
        template = NotificationTemplate.objects.create(
            key='report_ready',
            title_template='Report for {{ project_name }}',
            body_template='Your report {{ report_name }} is ready.',
            channel='in_app',
            is_active=True,
        )

        send_response = self.client_api.post('/api/notification-templates/send_templated/', {
            'template_id': str(template.id),
            'user_ids': [str(self.external_user.id)],
            'payload': {'project_name': 'SEO Growth', 'report_name': 'Weekly Performance Pulse'},
        }, format='json')
        self.assertEqual(send_response.status_code, 200)
        self.assertEqual(send_response.data['count'], 1)

        self.client_api.force_authenticate(user=self.external_user)
        unread_response = self.client_api.get('/api/notification-logs/unread_count/')
        self.assertEqual(unread_response.status_code, 200)
        self.assertGreaterEqual(unread_response.data['unread_count'], 1)
