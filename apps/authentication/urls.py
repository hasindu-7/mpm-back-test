from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    TenantTokenObtainPairView, ClientPortalTokenObtainView, MFAVerifyView,
    RoleViewSet, PermissionViewSet, UserViewSet,
    PasswordResetRequestView, PasswordResetConfirmView,
)

router = DefaultRouter()
router.register(r'roles', RoleViewSet, basename='role')
router.register(r'permissions', PermissionViewSet, basename='permission')
router.register(r'users', UserViewSet, basename='user')

urlpatterns = [
    path('auth/login/', TenantTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/client/login/', ClientPortalTokenObtainView.as_view(), name='client_portal_token_obtain_pair'),
    path('auth/mfa/verify/', MFAVerifyView.as_view(), name='mfa_verify'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/password-reset/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('auth/password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('', include(router.urls)),
]

