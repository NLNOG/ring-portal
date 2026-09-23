"""DRF permissions + helpers for ring-domain write scope.

Reads stay public. Writes require an authenticated account. Ring
administrators (Django staff/superuser or a linked RingUser with the legacy
``admin`` flag) may write anything; ordinary users may only create/update/
delete objects belonging to their own participant.
"""

from rest_framework import permissions

from ring.models import Machine
from ring.services.profiles import ring_user
from ring.zone import short


def is_ring_admin(user):
    if not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    ru = ring_user(user)
    return bool(ru and ru.is_ring_admin)


def org_of_user(user):
    """Participant the (authenticated) user belongs to, else None."""
    ru = ring_user(user)
    return ru.participant if ru else None


class OrgScopedPermission(permissions.BasePermission):
    """Authenticated-or-readonly writes, admin bypass, org-scoped objects.

    Subclasses must implement ``get_org(obj)`` returning the Participant an
    object belongs to.
    """

    def get_org(self, obj):
        raise NotImplementedError

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        if not request.user.is_authenticated:
            return False
        return is_ring_admin(request.user) or org_of_user(request.user) is not None

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        if is_ring_admin(request.user):
            return True
        org = org_of_user(request.user)
        return bool(org) and self.get_org(obj) == org


class ParticipantPermission(OrgScopedPermission):
    def get_org(self, obj):
        return obj


class RingUserPermission(OrgScopedPermission):
    def get_org(self, obj):
        return obj.participant


class MachinePermission(OrgScopedPermission):
    def get_org(self, obj):
        return obj.owner.participant


class SSHKeyPermission(OrgScopedPermission):
    def get_org(self, obj):
        return obj.user.participant


class SSHHostKeyPermission(OrgScopedPermission):
    def get_org(self, obj):
        return obj.machine.owner.participant


class ParticipantRemarkPermission(OrgScopedPermission):
    def get_org(self, obj):
        return obj.participant


class MachineRemarkPermission(OrgScopedPermission):
    def get_org(self, obj):
        return obj.machine.owner.participant


class IngestPermission(permissions.BasePermission):
    """POST-only ingest: admins may push anything; org users only their own.

    The ``hostname`` field(s) in the payload (short names) must resolve to a
    machine owned by the caller's participant.
    """

    def has_permission(self, request, view):
        if request.method not in permissions.SAFE_METHODS:
            if not request.user.is_authenticated:
                return False
            if is_ring_admin(request.user):
                return True
            org = org_of_user(request.user)
            if org is None:
                return False
            payload = request.data
            if not isinstance(payload, (list, dict)):
                return False
            items = payload if isinstance(payload, list) else [payload]
            short_names = {
                (item.get("hostname") or "").lower() for item in items
            }
            if not short_names:
                return False
            owned = set(
                Machine.objects.filter(
                    owner__participant=org,
                    active=True,
                ).values_list("hostname", flat=True)
            )
            owned_shorts = {short(h).lower() for h in owned}
            return short_names.issubset(owned_shorts)
        return True