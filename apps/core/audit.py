from apps.core.models import Tenant, TenantAuditLog


def _resolve_tenant(request):
    if request is None:
        return None
    subdomain = getattr(request, 'tenant_subdomain', None) or request.headers.get('X-Tenant-Subdomain')
    if not subdomain:
        return None
    return Tenant.objects.using('default').filter(subdomain=subdomain).first()


def create_audit_log(request, action, user=None, resource_type='', resource_id='', details=None, tenant=None):
    tenant = tenant or _resolve_tenant(request)
    if tenant is None:
        return

    actor = user or getattr(request, 'user', None)
    actor_email = ''
    if actor is not None and getattr(actor, 'is_authenticated', False):
        actor_email = actor.email

    ip_address = None
    if request is not None:
        ip_address = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR')

    TenantAuditLog.objects.using('default').create(
        tenant=tenant,
        user_email=actor_email or 'anonymous',
        action=action,
        resource_type=resource_type or '',
        resource_id=str(resource_id or ''),
        details=details or {},
        ip_address=ip_address,
    )
