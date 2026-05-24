from django.conf import settings
from django.db import connections
from .db_router import set_tenant_db
from .models import Tenant

class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(':')[0]
        subdomain = host.split('.')[0]
        request.tenant_subdomain = None
        
        # Development fallback: check Header or Query Param
        if subdomain in ['127', 'localhost']:
            subdomain = request.headers.get('X-Tenant-Subdomain') or request.GET.get('tenant')
        
        if subdomain:
            try:
                # We query 'default' DB to find the tenant
                tenant = Tenant.objects.using('default').get(subdomain=subdomain)
                db_name = tenant.db_name
                request.tenant_subdomain = tenant.subdomain
                
                # Dynamically register connection if not present
                if db_name not in connections:
                    new_db_settings = settings.DATABASES['default'].copy()
                    new_db_settings['NAME'] = db_name
                    connections.databases[db_name] = new_db_settings
                
                set_tenant_db(db_name)
            except Tenant.DoesNotExist:
                set_tenant_db('default')
        else:
            set_tenant_db('default')

        response = self.get_response(request)
        return response
