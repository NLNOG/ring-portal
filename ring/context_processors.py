from ring.models import Participant
from ring.services.profiles import (
    active_participant,
    active_participant_id,
    member_participant_ids,
    ring_user,
)


def user_can_manage(user):
    if not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    ru = ring_user(user)
    return bool(ru and ru.is_ring_admin)


def _user_organisations(user, active_id):
    """Orgs a member may act for, with the active one flagged."""
    orgs = []
    for pid in sorted(member_participant_ids(user)):
        participant = Participant.objects.filter(pk=pid).first()
        if participant is not None:
            orgs.append(
                {"participant": participant, "active": pid == active_id}
            )
    return orgs


def auth_context(request):
    from django.conf import settings

    active_participant_obj = None
    active_id = None
    organisations = []
    if request.user.is_authenticated and not user_can_manage(request.user):
        active_id = active_participant_id(request)
        active_participant_obj = active_participant(request)
        organisations = _user_organisations(request.user, active_id)

    return {
        "user_can_manage": user_can_manage(request.user),
        "pdb_enabled": bool(settings.PDB_CLIENT_ID and settings.PDB_REDIRECT_URL),
        "active_participant": active_participant_obj,
        "active_participant_id": active_id,
        "user_organisations": organisations,
    }