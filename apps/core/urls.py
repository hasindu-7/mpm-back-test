from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    TenantRegistrationView, TenantViewSet,
    SaaSPlanViewSet, FeatureFlagViewSet,
    SuperAdminDashboardView, WorkspaceBillingView,
)

router = DefaultRouter()
router.register(r'tenants', TenantViewSet, basename='tenant')
router.register(r'plans', SaaSPlanViewSet, basename='plan')
router.register(r'feature-flags', FeatureFlagViewSet, basename='feature-flag')

urlpatterns = [
    path('tenants/register/', TenantRegistrationView.as_view(), name='tenant-register'),
    path('platform/dashboard/', SuperAdminDashboardView.as_view(), name='platform-dashboard'),
    path('workspace-billing/', WorkspaceBillingView.as_view(), name='workspace-billing'),
    path('', include(router.urls)),
]

