from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.conf import settings
from django.db import connections
from apps.core.models import Tenant

class Command(BaseCommand):
    help = 'Runs migrations for all tenants'

    def handle(self, *args, **options):
        tenants = Tenant.objects.using('default').all()
        
        for tenant in tenants:
            self.stdout.write(f"Migrating tenant: {tenant.subdomain} (DB: {tenant.db_name})")
            
            db_name = tenant.db_name
            
            # Register connection if not present
            if db_name not in connections:
                new_db_settings = settings.DATABASES['default'].copy()
                new_db_settings['NAME'] = db_name
                connections.databases[db_name] = new_db_settings
            
            try:
                call_command('migrate', database=db_name, interactive=False)
                self.stdout.write(self.style.SUCCESS(f"Successfully migrated {tenant.subdomain}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed to migrate {tenant.subdomain}: {e}"))
