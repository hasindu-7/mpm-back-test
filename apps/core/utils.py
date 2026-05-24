import psycopg2
from django.conf import settings
from django.db import connections, connection
from django.core.management import call_command
from django.contrib.auth import get_user_model


def is_feature_enabled_for_tenant(request, feature_key, default_enabled=True):
    subdomain = getattr(request, 'tenant_subdomain', None) or request.headers.get('X-Tenant-Subdomain')
    if not subdomain:
        return default_enabled

    from .models import Tenant, FeatureFlag, Subscription

    tenant = Tenant.objects.using('default').filter(subdomain=subdomain).first()
    if not tenant:
        return default_enabled

    flag = FeatureFlag.objects.using('default').filter(key=feature_key).first()
    enabled = default_enabled if not flag else flag.enabled
    if not enabled:
        return False

    if flag and isinstance(flag.rollout_rules, dict):
        enabled_tenants = flag.rollout_rules.get('enabled_tenants', [])
        disabled_tenants = flag.rollout_rules.get('disabled_tenants', [])
        if enabled_tenants and tenant.subdomain not in enabled_tenants:
            return False
        if tenant.subdomain in disabled_tenants:
            return False

    subscription = Subscription.objects.using('default').filter(tenant=tenant, active=True).order_by('-updated_at').first()
    if subscription and isinstance(subscription.features, dict) and feature_key in subscription.features:
        return bool(subscription.features[feature_key])

    return enabled

def create_tenant_db(db_name):
    """
    Creates a physical PostgreSQL database.
    """
    # Get connection parameters from default database
    db_settings = settings.DATABASES['default']
    
    # Connect to 'postgres' or 'template1' to create the new database
    conn = psycopg2.connect(
        dbname='postgres',
        user=db_settings['USER'],
        password=db_settings['PASSWORD'],
        host=db_settings['HOST'],
        port=db_settings['PORT']
    )
    conn.autocommit = True
    cursor = conn.cursor()
    
    try:
        cursor.execute(f"CREATE DATABASE {db_name}")
    finally:
        cursor.close()
        conn.close()

def setup_tenant_db(db_name, admin_email, admin_password):
    """
    Registers the connection, runs migrations, bootstraps roles, and creates the admin user.
    """
    from .db_router import set_tenant_db
    set_tenant_db(db_name)

    # Register the connection dynamically if not present
    if db_name not in connections:
        new_db_settings = settings.DATABASES['default'].copy()
        new_db_settings['NAME'] = db_name
        connections.databases[db_name] = new_db_settings

    # Run migrations
    call_command('migrate', database=db_name, interactive=False)

    # Bootstrap roles and permissions
    from apps.authentication.models import Role, Permission
    
    # 1. Define default permissions
    permissions_data = [
        ('can_manage_roles', 'Can Manage Roles', 'Ability to create and edit custom roles.'),
        ('can_manage_users', 'Can Manage Users', 'Ability to add and remove team members.'),
        ('can_manage_projects', 'Can Manage Projects', 'Ability to create and edit projects.'),
        ('can_view_projects', 'Can View Projects', 'Ability to view project details.'),
        ('can_manage_billing', 'Can Manage Billing', 'Ability to create and update billing records.'),
        ('can_manage_hr', 'Can Manage HR', 'Ability to administer HR and payroll records.'),
        ('can_view_audit_logs', 'Can View Audit Logs', 'Ability to review immutable tenant audit logs.'),
    ]
    
    perms_objs = {}
    for codename, name, desc in permissions_data:
        obj, _ = Permission.objects.using(db_name).get_or_create(
            codename=codename,
            defaults={'name': name, 'description': desc}
        )
        perms_objs[codename] = obj

    # 2. Define default roles
    roles_data = [
        ('Owner', 'Full access to the agency and settings.', False, permissions_data),
        ('Agency Manager', 'Full management of projects and users.', False, permissions_data),
        ('Project Manager', 'Manage specific projects and tasks.', False, [('can_manage_projects', '', ''), ('can_view_projects', '', '')]),
        ('Team Member', 'Execute assigned tasks and log time.', False, [('can_view_projects', '', '')]),
        ('Client', 'Access to specific project progress.', False, [('can_view_projects', '', '')]),
    ]

    owner_role = None
    for name, desc, is_custom, role_perms in roles_data:
        role, _ = Role.objects.using(db_name).get_or_create(
            name=name,
            defaults={'description': desc, 'is_custom': is_custom}
        )
        # Assign permissions to roles
        for p_code, _, _ in role_perms:
            role.permissions.add(perms_objs[p_code])
        
        if name == 'Owner':
            owner_role = role

    # Create admin user in the new database
    User = get_user_model()
    if not User.objects.using(db_name).filter(email=admin_email).exists():
        User.objects.db_manager(db_name).create_superuser(
            email=admin_email,
            password=admin_password,
            category='agency',
            role_obj=owner_role
        )
