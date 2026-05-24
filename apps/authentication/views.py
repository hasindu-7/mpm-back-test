from rest_framework import viewsets, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from django.core.mail import send_mail
from django.conf import settings
from .models import Role, Permission, User, PasswordResetToken
from .serializers import (
    TenantTokenObtainPairSerializer, 
    ClientPortalTokenObtainSerializer,
    MFAVerifySerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    RoleSerializer, 
    PermissionSerializer,
    UserSerializer
)
from .permissions import HasTenantPermission
from apps.core.audit import create_audit_log

class IsAgencyAdmin(permissions.BasePermission):
    """
    Custom permission to only allow Agency Owners and Managers.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Superusers are always allowed
        if request.user.is_superuser:
            return True
            
        role_name = request.user.role_obj.name if request.user.role_obj else ""
        return request.user.category == 'agency' and role_name in ['Owner', 'Agency Manager']

class TenantTokenObtainPairView(TokenObtainPairView):
    serializer_class = TenantTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK and not response.data.get('mfa_required'):
            create_audit_log(
                request=request,
                action='auth.login.success',
                resource_type='auth',
                details={'email': request.data.get('email')},
            )
        return response


class ClientPortalTokenObtainView(TokenObtainPairView):
    serializer_class = ClientPortalTokenObtainSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK and not response.data.get('mfa_required'):
            create_audit_log(
                request=request,
                action='auth.login.client_portal.success',
                resource_type='auth',
                details={'email': request.data.get('email')},
            )
        return response


class MFAVerifyView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = MFAVerifySerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        tokens = serializer.build_tokens()

        create_audit_log(
            request=request,
            user=serializer.validated_data['user'],
            action='auth.mfa.success',
            resource_type='auth',
            details={'challenge_token': str(serializer.validated_data['challenge_token'])},
        )

        return Response(tokens, status=status.HTTP_200_OK)

class RoleViewSet(viewsets.ModelViewSet):
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [permissions.IsAuthenticated, HasTenantPermission]
    required_permission = 'can_manage_roles'

    def get_queryset(self):
        return Role.objects.all()

    def perform_create(self, serializer):
        role = serializer.save()
        create_audit_log(
            request=self.request,
            action='rbac.role.created',
            resource_type='role',
            resource_id=role.id,
            details={'name': role.name},
        )

    def perform_update(self, serializer):
        role = serializer.save()
        create_audit_log(
            request=self.request,
            action='rbac.role.updated',
            resource_type='role',
            resource_id=role.id,
            details={'name': role.name},
        )

    def perform_destroy(self, instance):
        role_id = instance.id
        role_name = instance.name
        super().perform_destroy(instance)
        create_audit_log(
            request=self.request,
            action='rbac.role.deleted',
            resource_type='role',
            resource_id=role_id,
            details={'name': role_name},
        )

class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    permission_classes = [permissions.IsAuthenticated, HasTenantPermission]
    required_permission = 'can_manage_roles'

class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, HasTenantPermission]
    required_permission = 'can_manage_users'

    def perform_create(self, serializer):
        user = serializer.save()
        create_audit_log(
            request=self.request,
            action='auth.user.created',
            resource_type='user',
            resource_id=user.id,
            details={'email': user.email},
        )

    def perform_update(self, serializer):
        user = serializer.save()
        create_audit_log(
            request=self.request,
            action='auth.user.updated',
            resource_type='user',
            resource_id=user.id,
            details={'email': user.email},
        )

    def perform_destroy(self, instance):
        user_id = instance.id
        user_email = instance.email
        super().perform_destroy(instance)
        create_audit_log(
            request=self.request,
            action='auth.user.deleted',
            resource_type='user',
            resource_id=user_id,
            details={'email': user_email},
        )


class PasswordResetRequestView(APIView):
    """
    POST /auth/password-reset/
    Body: { "email": "...", "subdomain": "..." }
    Always returns 200 to prevent user enumeration.
    Sends a password reset link by email when the user exists.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data['email']
        try:
            user = User.objects.get(email=email)
            reset_token = PasswordResetToken.create_for_user(user)

            # Build reset URL – the frontend /reset-password?token=<uuid> page
            frontend_origin = request.headers.get('Origin', settings.DEFAULT_FROM_EMAIL)
            reset_url = f"{frontend_origin}/reset-password?token={reset_token.token}"

            send_mail(
                subject='Reset your MPM password',
                message=(
                    f"Hi {user.first_name or user.email},\n\n"
                    f"Click the link below to reset your password. "
                    f"This link expires in 60 minutes.\n\n"
                    f"{reset_url}\n\n"
                    f"If you did not request a password reset, you can safely ignore this email."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )

            create_audit_log(
                request=request,
                action='auth.password_reset.requested',
                resource_type='user',
                resource_id=user.id,
                details={'email': user.email},
            )
        except User.DoesNotExist:
            pass  # Silently ignore to prevent user enumeration

        return Response(
            {'detail': 'If an account with that email exists, a reset link has been sent.'},
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    """
    POST /auth/password-reset/confirm/
    Body: { "token": "<uuid>", "new_password": "..." }
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token_uuid = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']

        try:
            reset_token = PasswordResetToken.objects.select_related('user').get(token=token_uuid)
        except PasswordResetToken.DoesNotExist:
            return Response({'detail': 'Invalid or expired reset link.'}, status=status.HTTP_400_BAD_REQUEST)

        if reset_token.is_used or reset_token.is_expired:
            return Response({'detail': 'This reset link has already been used or has expired.'}, status=status.HTTP_400_BAD_REQUEST)

        user = reset_token.user
        user.set_password(new_password)
        user.save(update_fields=['password'])
        reset_token.consume()

        create_audit_log(
            request=request,
            action='auth.password_reset.confirmed',
            resource_type='user',
            resource_id=user.id,
            details={'email': user.email},
        )

        return Response({'detail': 'Password has been reset successfully.'}, status=status.HTTP_200_OK)
