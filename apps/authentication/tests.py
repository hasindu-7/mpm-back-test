from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.authentication.models import MFAChallenge, Permission, Role
from apps.authentication.permissions import HasTenantPermission
from apps.core.models import Tenant


class MFAChallengeTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(
			email='mfa-user@example.com',
			password='Password123!'
		)

	def test_mfa_challenge_verify_once(self):
		challenge = MFAChallenge.create_for_user(self.user, code='123456', ttl_minutes=5)

		self.assertTrue(challenge.verify('123456'))
		self.assertFalse(challenge.verify('123456'))


class TenantPermissionTests(TestCase):
	def setUp(self):
		self.permission = Permission.objects.create(
			codename='can_manage_projects',
			name='Can Manage Projects',
		)
		self.role = Role.objects.create(name='Project Manager')
		self.role.permissions.add(self.permission)

		self.user = get_user_model().objects.create_user(
			email='pm@example.com',
			password='Password123!',
			role_obj=self.role,
		)

		self.factory = APIRequestFactory()
		self.guard = HasTenantPermission()

	def test_has_tenant_permission_true_when_role_has_codename(self):
		request = self.factory.get('/api/projects/')
		request.user = self.user

		class DummyView:
			required_permission = 'can_manage_projects'

		self.assertTrue(self.guard.has_permission(request, DummyView()))

	def test_has_tenant_permission_false_when_missing_codename(self):
		request = self.factory.get('/api/projects/')
		request.user = self.user

		class DummyView:
			required_permission = 'can_manage_billing'

		self.assertFalse(self.guard.has_permission(request, DummyView()))


class ClientPortalLoginTests(TestCase):
	def setUp(self):
		self.client_api = APIClient()
		self.subdomain = 'acme'
		Tenant.objects.create(
			name='Acme Workspace',
			subdomain=self.subdomain,
			db_name='default',
			db_user='tenant_user',
			db_password='tenant_pass'
		)

		self.external_user = get_user_model().objects.create_user(
			email='external-client@example.com',
			password='Password123!',
			category='external',
		)
		self.agency_user = get_user_model().objects.create_user(
			email='agency-user@example.com',
			password='Password123!',
			category='agency',
		)

	def test_client_portal_login_allows_external_and_sets_auth_context_claim(self):
		response = self.client_api.post(
			'/api/auth/client/login/',
			{'email': self.external_user.email, 'password': 'Password123!', 'subdomain': self.subdomain},
			format='json',
			HTTP_X_TENANT_SUBDOMAIN=self.subdomain,
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data.get('auth_context'), 'client_portal')
		access = AccessToken(response.data['access'])
		self.assertEqual(access.get('auth_context'), 'client_portal')
		self.assertEqual(access.get('category'), 'external')

	def test_client_portal_login_blocks_non_external_users(self):
		response = self.client_api.post(
			'/api/auth/client/login/',
			{'email': self.agency_user.email, 'password': 'Password123!', 'subdomain': self.subdomain},
			format='json',
			HTTP_X_TENANT_SUBDOMAIN=self.subdomain,
		)

		self.assertEqual(response.status_code, 400)
		detail = response.data.get('detail', '')
		self.assertIn('external client users', str(detail))
