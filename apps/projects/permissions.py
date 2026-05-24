from rest_framework import permissions
from .models import ProjectMember

class HasProjectPermission(permissions.BasePermission):
    """
    Checks if the user has a specific permission via their ProjectRole.
    Expects the view to specify `required_permission` and either have a `project_id`
    in URL kwargs or be acting on a Project instance.
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
            
        if request.user.is_superuser or (request.user.role_obj and request.user.role_obj.name == 'Owner'):
            return True

        required_perm = getattr(view, 'required_project_permission', None)
        if not required_perm:
            return True # If view doesn't specify, we pass

        project_id = view.kwargs.get('project_id') or request.query_params.get('project_id')
        if not project_id and 'pk' in view.kwargs:
            # Maybe it's a detail view for a project
            project_id = view.kwargs['pk']
            
        if not project_id:
            # If we can't figure out the project, deny
            return False

        try:
            member = ProjectMember.objects.get(project_id=project_id, user=request.user)
            if not member.project_role:
                return False
            
            return member.project_role.permissions.filter(codename=required_perm).exists()
        except ProjectMember.DoesNotExist:
            return False

    def has_object_permission(self, request, view, obj):
        if not request.user.is_authenticated:
            return False
            
        if request.user.is_superuser or (request.user.role_obj and request.user.role_obj.name == 'Owner'):
            return True
            
        required_perm = getattr(view, 'required_project_permission', None)
        if not required_perm:
            return True

        project = getattr(obj, 'project', obj if hasattr(obj, 'status') else None)
        if not project:
            return False
            
        try:
            member = ProjectMember.objects.get(project=project, user=request.user)
            if not member.project_role:
                return False
            return member.project_role.permissions.filter(codename=required_perm).exists()
        except ProjectMember.DoesNotExist:
            return False
