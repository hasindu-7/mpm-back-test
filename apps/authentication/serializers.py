from rest_framework import serializers
from django.utils.crypto import get_random_string
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import Role, Permission, User
from .models import MFAChallenge
from apps.core.db_router import get_tenant_db
from apps.core.models import Tenant

class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ['codename', 'name', 'description']

class RoleSerializer(serializers.ModelSerializer):
    permissions = PermissionSerializer(many=True, read_only=True)
    permission_codenames = serializers.ListField(
        child=serializers.CharField(),
        write_only=True,
        required=False
    )

    class Meta:
        model = Role
        fields = ['id', 'name', 'description', 'is_custom', 'permissions', 'permission_codenames']
        read_only_fields = ['id', 'is_custom']

    def create(self, validated_data):
        permission_codenames = validated_data.pop('permission_codenames', [])
        role = Role.objects.create(**validated_data)
        if permission_codenames:
            permissions = Permission.objects.filter(codename__in=permission_codenames)
            role.permissions.set(permissions)
        return role

    def update(self, instance, validated_data):
        permission_codenames = validated_data.pop('permission_codenames', None)
        instance = super().update(instance, validated_data)
        if permission_codenames is not None:
            permissions = Permission.objects.filter(codename__in=permission_codenames)
            instance.permissions.set(permissions)
        return instance

class UserSerializer(serializers.ModelSerializer):
    role = RoleSerializer(source='role_obj', read_only=True)
    role_id = serializers.UUIDField(write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'category', 'role', 'role_id', 'password', 'daily_working_hours']


    def create(self, validated_data):
        role_id = validated_data.pop('role_id', None)
        password = validated_data.pop('password', None)
        
        user = User(**validated_data)
        if password:
            user.set_password(password)
        
        if role_id:
            try:
                role = Role.objects.get(id=role_id)
                user.role_obj = role
            except Role.DoesNotExist:
                raise serializers.ValidationError({"role_id": "Role does not exist."})
        
        user.save()
        return user

    def update(self, instance, validated_data):
        role_id = validated_data.pop('role_id', None)
        password = validated_data.pop('password', None)

        if password:
            instance.set_password(password)
        
        if role_id:
            try:
                role = Role.objects.get(id=role_id)
                instance.role_obj = role
            except Role.DoesNotExist:
                raise serializers.ValidationError({"role_id": "Role does not exist."})
        
        return super().update(instance, validated_data)

class TenantTokenObtainPairSerializer(TokenObtainPairSerializer):
    subdomain = serializers.CharField(write_only=True)

    def validate(self, attrs):
        request = self.context.get('request')
        requested_subdomain = attrs.get('subdomain') or (request.headers.get('X-Tenant-Subdomain') if request else None)
        active_db = get_tenant_db()

        if not requested_subdomain:
            raise serializers.ValidationError({'subdomain': 'Workspace subdomain is required.'})

        try:
            tenant = Tenant.objects.using('default').get(subdomain=requested_subdomain)
        except Tenant.DoesNotExist as exc:
            raise serializers.ValidationError({'subdomain': 'Unknown workspace subdomain.'}) from exc

        if tenant.db_name != active_db:
            raise serializers.ValidationError({'detail': 'Tenant context mismatch. Verify workspace subdomain.'})

        data = super().validate(attrs)
        user = self.user

        if user.mfa_enabled:
            otp = get_random_string(length=6, allowed_chars='0123456789')
            challenge = MFAChallenge.create_for_user(user=user, code=otp, ttl_minutes=5)
            # Placeholder transport until email/SMS provider is integrated.
            payload = {
                'mfa_required': True,
                'challenge_token': str(challenge.challenge_token),
                'detail': 'MFA verification required.',
            }
            if settings.DEBUG:
                payload['otp_debug'] = otp
            return payload

        refresh = RefreshToken(data['refresh'])
        refresh['tenant'] = requested_subdomain
        access = refresh.access_token
        access['tenant'] = requested_subdomain
        data['refresh'] = str(refresh)
        data['access'] = str(access)
        data['mfa_required'] = False
        data['tenant'] = requested_subdomain
        return data

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['role'] = user.role_obj.name if user.role_obj else None
        token['category'] = user.category
        token['is_superuser'] = bool(user.is_superuser)
        token['first_name'] = user.first_name or ''
        token['last_name'] = user.last_name or ''
        token['email'] = user.email or ''
        if user.role_obj:
            token['permissions'] = list(
                user.role_obj.permissions.values_list('codename', flat=True)
            )
        else:
            token['permissions'] = []
        return token


class ClientPortalTokenObtainSerializer(TenantTokenObtainPairSerializer):
    """
    Dedicated login context for client portal users.
    Only external users can authenticate via this flow.
    """

    def validate(self, attrs):
        data = super().validate(attrs)
        if data.get('mfa_required'):
            return data

        if self.user.category != 'external':
            raise serializers.ValidationError({'detail': 'Client portal login is only available for external client users.'})

        refresh = RefreshToken(data['refresh'])
        refresh['auth_context'] = 'client_portal'
        access = refresh.access_token
        access['auth_context'] = 'client_portal'
        data['refresh'] = str(refresh)
        data['access'] = str(access)
        data['auth_context'] = 'client_portal'
        return data


class MFAVerifySerializer(serializers.Serializer):
    challenge_token = serializers.UUIDField()
    code = serializers.CharField(min_length=6, max_length=6)

    def validate(self, attrs):
        challenge_token = attrs['challenge_token']
        code = attrs['code']
        request = self.context.get('request')
        requested_subdomain = request.headers.get('X-Tenant-Subdomain') or request.data.get('subdomain')
        active_db = get_tenant_db()

        if not requested_subdomain:
            raise serializers.ValidationError({'subdomain': 'Workspace subdomain is required.'})

        try:
            tenant = Tenant.objects.using('default').get(subdomain=requested_subdomain)
        except Tenant.DoesNotExist as exc:
            raise serializers.ValidationError({'subdomain': 'Unknown workspace subdomain.'}) from exc

        if tenant.db_name != active_db:
            raise serializers.ValidationError({'detail': 'Tenant context mismatch. Verify workspace subdomain.'})

        challenge = MFAChallenge.objects.filter(challenge_token=challenge_token).select_related('user').first()
        if not challenge:
            raise serializers.ValidationError({'detail': 'Invalid MFA challenge.'})

        if not challenge.verify(code):
            raise serializers.ValidationError({'detail': 'Invalid or expired MFA code.'})

        attrs['user'] = challenge.user
        attrs['tenant'] = tenant
        return attrs

    def build_tokens(self):
        user = self.validated_data['user']
        tenant = self.validated_data['tenant']

        refresh = RefreshToken.for_user(user)
        refresh['role'] = user.role_obj.name if user.role_obj else None
        refresh['category'] = user.category
        refresh['tenant'] = tenant.subdomain
        refresh['is_superuser'] = bool(user.is_superuser)

        access = refresh.access_token
        access['role'] = user.role_obj.name if user.role_obj else None
        access['category'] = user.category
        access['tenant'] = tenant.subdomain
        access['is_superuser'] = bool(user.is_superuser)

        return {
            'refresh': str(refresh),
            'access': str(access),
            'mfa_required': False,
            'tenant': tenant.subdomain,
        }


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.UUIDField()
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_new_password(self, value):
        validate_password(value)
        return value
