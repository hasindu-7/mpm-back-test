from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connections

from apps.authentication.models import Permission, Role
from apps.core.models import Tenant


DEFAULT_PASSWORD = "DemoPass@123"


PERMISSIONS_DATA = [
    ("can_manage_roles", "Can Manage Roles", "Ability to create and edit custom roles."),
    ("can_manage_users", "Can Manage Users", "Ability to add and remove team members."),
    ("can_manage_projects", "Can Manage Projects", "Ability to create and edit projects."),
    ("can_view_projects", "Can View Projects", "Ability to view project details."),
    ("can_manage_billing", "Can Manage Billing", "Ability to create and update billing records."),
    ("can_manage_hr", "Can Manage HR", "Ability to administer HR and payroll records."),
    ("can_view_audit_logs", "Can View Audit Logs", "Ability to review immutable tenant audit logs."),
]


ROLE_RULES = [
    ("Owner", "Full access to the agency and settings.", [code for code, _, _ in PERMISSIONS_DATA]),
    ("Agency Manager", "Full management of projects and users.", [code for code, _, _ in PERMISSIONS_DATA]),
    ("Project Manager", "Manage specific projects and tasks.", ["can_manage_projects", "can_view_projects"]),
    ("Team Member", "Execute assigned tasks and log time.", ["can_view_projects"]),
    ("Client", "Access to specific project progress.", ["can_view_projects"]),
]


def ensure_db_alias(db_name):
    if db_name not in connections:
        db_cfg = settings.DATABASES["default"].copy()
        db_cfg["NAME"] = db_name
        connections.databases[db_name] = db_cfg


def ensure_roles_and_permissions(db_alias):
    perms = {}
    for codename, name, description in PERMISSIONS_DATA:
        obj, _ = Permission.objects.using(db_alias).get_or_create(
            codename=codename,
            defaults={"name": name, "description": description},
        )
        perms[codename] = obj

    role_map = {}
    for role_name, description, perm_codes in ROLE_RULES:
        role, _ = Role.objects.using(db_alias).get_or_create(
            name=role_name,
            defaults={"description": description, "is_custom": False},
        )
        for code in perm_codes:
            role.permissions.add(perms[code])
        role_map[role_name] = role

    return role_map


def upsert_user(db_alias, email, password, category, role_obj=None, is_staff=False, is_superuser=False):
    user_model = get_user_model()
    user = user_model.objects.using(db_alias).filter(email=email).first()
    if user is None:
        user = user_model.objects.db_manager(db_alias).create_user(
            email=email,
            password=password,
            category=category,
            role_obj=role_obj,
        )
    else:
        user.category = category
        user.role_obj = role_obj
        user.set_password(password)

    user.is_staff = is_staff
    user.is_superuser = is_superuser
    user.is_active = True
    user.save(using=db_alias)
    return user


class Command(BaseCommand):
    help = "Create demo users for each role/category type in each tenant and print credentials."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help="Password to assign to all seeded users.",
        )

    def handle(self, *args, **options):
        password = options["password"]

        tenants = list(Tenant.objects.using("default").all().order_by("subdomain"))
        if not tenants:
            tenants = [
                Tenant.objects.using("default").create(
                    name="Demo Workspace",
                    subdomain="demo",
                    db_name="default",
                    db_user=settings.DATABASES["default"].get("USER", "postgres"),
                    db_password=settings.DATABASES["default"].get("PASSWORD", ""),
                )
            ]

        seeded = []

        for tenant in tenants:
            db_alias = tenant.db_name
            ensure_db_alias(db_alias)
            role_map = ensure_roles_and_permissions(db_alias)

            templates = [
                (f"owner@{tenant.subdomain}.local", "agency", "Owner"),
                (f"manager@{tenant.subdomain}.local", "agency", "Agency Manager"),
                (f"pm@{tenant.subdomain}.local", "agency", "Project Manager"),
                (f"team@{tenant.subdomain}.local", "agency", "Team Member"),
                (f"client-agency@{tenant.subdomain}.local", "agency", "Client"),
                (f"client-external@{tenant.subdomain}.local", "external", "Client"),
            ]

            for email, category, role_name in templates:
                role_obj = role_map.get(role_name)
                upsert_user(
                    db_alias=db_alias,
                    email=email,
                    password=password,
                    category=category,
                    role_obj=role_obj,
                    is_staff=(role_name in ["Owner", "Agency Manager"]),
                    is_superuser=False,
                )
                seeded.append((tenant.subdomain, email, password, tenant.subdomain, f"{category}/{role_name}"))

        self.stdout.write(self.style.SUCCESS("Demo users seeded successfully."))
        self.stdout.write("\nCredentials:")
        for tenant_subdomain, email, pwd, login_subdomain, user_type in seeded:
            self.stdout.write(
                f"- tenant={tenant_subdomain} | email={email} | password={pwd} | login_subdomain={login_subdomain} | type={user_type}"
            )