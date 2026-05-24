from django.core.exceptions import ValidationError
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory
from rest_framework.test import APIClient

from apps.core.models import Tenant, TenantAuditLog, SaaSPlan, FeatureFlag, Subscription
from apps.core.permissions import IsSuperAdminUser, HasFeatureEntitlement


class TenantAuditLogTests(TestCase):
	def setUp(self):
		self.tenant = Tenant.objects.create(
			name='Acme Agency',
			subdomain='acme',
			db_name='tenant_acme',
			db_user='postgres',
			db_password='password',
		)

	def test_audit_log_is_immutable_on_update(self):
		log = TenantAuditLog.objects.create(
			tenant=self.tenant,
			user_email='owner@acme.com',
			action='project.updated',
			resource_type='project',
			resource_id='1',
		)

		log.action = 'project.deleted'
		with self.assertRaises(ValidationError):
			log.save()

	def test_audit_log_is_immutable_on_delete(self):
		log = TenantAuditLog.objects.create(
			tenant=self.tenant,
			user_email='owner@acme.com',
			action='project.created',
			resource_type='project',
			resource_id='2',
		)

		with self.assertRaises(ValidationError):
			log.delete()


class IsSuperAdminUserPermissionTests(TestCase):
	def setUp(self):
		user_model = get_user_model()
		self.superuser = user_model.objects.create_superuser(
			email='root@example.com',
			password='Password123!'
		)
		self.regular_user = user_model.objects.create_user(
			email='member@example.com',
			password='Password123!'
		)
		self.factory = APIRequestFactory()
		self.permission = IsSuperAdminUser()

	def test_allows_superuser(self):
		request = self.factory.get('/api/tenants/')
		request.user = self.superuser
		self.assertTrue(self.permission.has_permission(request, view=None))

	def test_denies_regular_user(self):
		request = self.factory.get('/api/tenants/')
		request.user = self.regular_user
		self.assertFalse(self.permission.has_permission(request, view=None))


class PhaseSevenPlatformApiTests(TestCase):
	def setUp(self):
		user_model = get_user_model()
		self.superuser = user_model.objects.create_superuser(
			email='root-phase7@example.com',
			password='Password123!',
		)
		self.agency_user = user_model.objects.create_user(
			email='agency-phase7@example.com',
			password='Password123!',
			category='agency',
		)

		self.tenant = Tenant.objects.create(
			name='Nimbus Studio',
			subdomain='nimbus',
			db_name='tenant_nimbus',
			db_user='postgres',
			db_password='password',
		)

		self.plan = SaaSPlan.objects.create(
			code='growth',
			name='Growth',
			monthly_price='2500.00',
			per_seat_price='60.00',
			included_seats=5,
			trial_days=14,
			is_active=True,
		)

		self.subscription = Subscription.objects.create(
			tenant=self.tenant,
			name='Growth',
			plan=self.plan,
			status='trialing',
			seat_count=7,
			active=True,
		)

		self.flag = FeatureFlag.objects.create(
			key='hr_leave_active',
			name='HR Leave Planner',
			enabled=True,
		)

		self.client_api = APIClient()

	def test_super_admin_dashboard_endpoint(self):
		self.client_api.force_authenticate(user=self.superuser)
		response = self.client_api.get('/api/platform/dashboard/')
		self.assertEqual(response.status_code, 200)
		self.assertIn('stats', response.data)
		self.assertIn('system_health', response.data)

	def test_feature_flag_toggle(self):
		self.client_api.force_authenticate(user=self.superuser)
		response = self.client_api.post(f'/api/feature-flags/{self.flag.id}/toggle/', {}, format='json')
		self.assertEqual(response.status_code, 200)
		self.flag.refresh_from_db()
		self.assertFalse(self.flag.enabled)

	def test_workspace_billing_actions(self):
		self.client_api.force_authenticate(user=self.agency_user)
		response = self.client_api.get('/api/workspace-billing/', HTTP_X_TENANT_SUBDOMAIN='nimbus')
		self.assertEqual(response.status_code, 200)
		self.assertIn('subscription', response.data)

		seat_response = self.client_api.post(
			'/api/workspace-billing/',
			{'action': 'update_seats', 'seat_count': 9},
			format='json',
			HTTP_X_TENANT_SUBDOMAIN='nimbus',
		)
		self.assertEqual(seat_response.status_code, 200)
		self.assertEqual(seat_response.data['seat_count'], 9)

		checkout_response = self.client_api.post(
			'/api/workspace-billing/',
			{'action': 'create_checkout_session'},
			format='json',
			HTTP_X_TENANT_SUBDOMAIN='nimbus',
		)
		self.assertEqual(checkout_response.status_code, 200)
		self.assertEqual(checkout_response.data['mode'], 'mock')


class FeatureEntitlementPermissionTests(TestCase):
	def setUp(self):
		user_model = get_user_model()
		self.user = user_model.objects.create_user(
			email='feature-user@example.com',
			password='Password123!',
			category='agency',
		)
		self.tenant = Tenant.objects.create(
			name='Signal Labs',
			subdomain='signal',
			db_name='tenant_signal',
			db_user='postgres',
			db_password='password',
		)
		self.permission = HasFeatureEntitlement()
		self.factory = APIRequestFactory()

	def _make_view(self, feature_key):
		class DummyView:
			required_feature = feature_key
		return DummyView()

	def test_allows_when_flag_missing_default_true(self):
		request = self.factory.get('/api/hr/employees/', HTTP_X_TENANT_SUBDOMAIN='signal')
		request.user = self.user
		self.assertTrue(self.permission.has_permission(request, self._make_view('hr_leave_active')))

	def test_denies_when_flag_disabled(self):
		FeatureFlag.objects.create(
			key='hr_leave_active',
			name='HR Leave Planner',
			enabled=False,
		)
		request = self.factory.get('/api/hr/employees/', HTTP_X_TENANT_SUBDOMAIN='signal')
		request.user = self.user
		self.assertFalse(self.permission.has_permission(request, self._make_view('hr_leave_active')))
