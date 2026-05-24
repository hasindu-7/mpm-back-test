from rest_framework import permissions


class HasTenantPermission(permissions.BasePermission):
    """
    Checks codename-based permissions attached to a user's role.
    Views should define `required_permission` for this permission to enforce.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        required_permission = getattr(view, 'required_permission', None)
        if not required_permission:
            return True

        role = getattr(user, 'role_obj', None)
        if not role:
            return False

        return role.permissions.filter(codename=required_permission).exists()
