from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken


class TenantScopedJWTAuthentication(JWTAuthentication):
    """
    Enforces that JWTs are used only for the tenant they were issued for.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, validated_token = result
        token_tenant = validated_token.get('tenant')
        request_tenant = getattr(request, 'tenant_subdomain', None) or request.headers.get('X-Tenant-Subdomain')

        if not token_tenant or not request_tenant:
            raise InvalidToken('Tenant context is missing.')

        if token_tenant != request_tenant:
            raise InvalidToken('Token tenant does not match request tenant.')

        return user, validated_token
