from rest_framework import permissions
from .utils import is_feature_enabled_for_tenant


class IsSuperAdminUser(permissions.BasePermission):
    """
    Allows access only to authenticated Django superusers.
    """

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_superuser)


class HasFeatureEntitlement(permissions.BasePermission):
    message = 'This feature is not enabled for your workspace plan.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        required_feature = getattr(view, 'required_feature', None)
        if not required_feature:
            return True

        return is_feature_enabled_for_tenant(request, required_feature, default_enabled=True)
