from rest_framework import permissions


class IsAdminUserRole(permissions.BasePermission):
    """
    Allows access only to users with role == 'ADMIN' or superuser/staff.
    Students calling these routes receive HTTP 403 Forbidden.
    """
    message = "Admin access required. You do not have permission to perform this action."

    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.is_admin_user()
        )
