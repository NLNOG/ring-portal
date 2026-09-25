"""App-side extension data for legacy ring rows (SQLite).

The legacy ring tables live in the read-only MySQL database; everything this
tool knows about those rows that has no legacy column (PeeringDB identities,
the Django auth link, backfilled participant ASNs) is stored in the default
SQLite database keyed by the legacy integer primary key.
"""

import os

from django.conf import settings
from django.contrib.auth import get_user_model

from ring.models import (
    Machine,
    Participant,
    ParticipantProfile,
    PeeringDBNetwork,
    RingUser,
    RingUserProfile,
)


class LegacyReadonlyError(RuntimeError):
    pass


def legacy_writable():
    """Whether writes to the Group A (legacy) tables are permitted.

    Mirror mode (no ``legacy`` alias configured) writes to the SQLite mirror
    and is always allowed. When reading the production MySQL database, writes
    are denied unless ``RING_LEGACY_WRITE_ENABLED=1`` is set.
    """
    if "legacy" not in settings.DATABASES:
        return True
    return os.environ.get("RING_LEGACY_WRITE_ENABLED") == "1"


def assert_legacy_writable():
    if not legacy_writable():
        raise LegacyReadonlyError(
            "This operation writes legacy ring data, which is read-only in this "
            "deployment. Set RING_LEGACY_WRITE_ENABLED=1 only once the MySQL "
            "account actually has the write grants."
        )


def _authenticated(user):
    if user is None:
        return None
    if not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "is_anonymous", False):
        return None
    return user


def profile_for_user(user):
    """RingUserProfile linked to a Django user, or None."""
    user = _authenticated(user)
    if user is None:
        return None
    try:
        return user.ring_profile
    except (get_user_model().DoesNotExist, RingUserProfile.DoesNotExist):
        return None


def ring_user(user):
    """Legacy RingUser linked to a Django user, or None."""
    profile = profile_for_user(user)
    if profile is None:
        return None
    return RingUser.objects.filter(pk=profile.ring_user_id).first()


def profile_for_ring_user(ring_user_id):
    """RingUserProfile for a legacy user id, or None."""
    return RingUserProfile.objects.filter(ring_user_id=ring_user_id).first()


def ring_user_for_pdb(peeringdb_id):
    """(RingUser, RingUserProfile) bound to a PeeringDB identity, or (None, None)."""
    profile = RingUserProfile.objects.filter(peeringdb_id=peeringdb_id).first()
    if profile is None:
        return None, None
    return RingUser.objects.filter(pk=profile.ring_user_id).first(), profile


def link_ring_user(django_user, ring_user, peeringdb_id=None, peeringdb_net_id=None):
    """Attach (or update) the SQLite profile for a legacy RingUser."""
    profile, _ = RingUserProfile.objects.get_or_create(
        django_user=django_user, defaults={"ring_user_id": ring_user.pk}
    )
    profile.ring_user_id = ring_user.pk
    if peeringdb_id is not None:
        profile.peeringdb_id = peeringdb_id
    if peeringdb_net_id is not None:
        profile.peeringdb_net_id = peeringdb_net_id
    profile.save()
    return profile


def link_pdb_network(django_user, peeringdb_net_id, asn=None, net_name=None,
                     participant_id=None, participant=None):
    """Record that a user acts for one PeeringDB network / participant.

    ``participant`` (a legacy Participant) is resolved to its integer pk and
    stored in the app-side column. The first linked network is remembered as
    the profile's primary ``peeringdb_net_id`` for backwards compatibility.
    """
    pid = getattr(participant, "pk", participant_id)
    link, _ = PeeringDBNetwork.objects.get_or_create(
        django_user=django_user,
        peeringdb_net_id=peeringdb_net_id,
        defaults={
            "asn": asn or 0,
            "net_name": net_name,
            "participant_id": pid,
        },
    )
    if asn is not None:
        link.asn = asn
    if net_name is not None:
        link.net_name = net_name
    if pid is not None:
        link.participant_id = pid
    link.save()
    profile = RingUserProfile.objects.filter(django_user=django_user).first()
    if (
        profile is not None
        and profile.peeringdb_net_id is None
        and peeringdb_net_id
    ):
        profile.peeringdb_net_id = peeringdb_net_id
        profile.save()
    return link


def pdb_networks(user):
    """PeeringDBNetwork links for a user, in link order."""
    user = _authenticated(user)
    if user is None:
        return []
    return list(PeeringDBNetwork.objects.filter(django_user=user))


def member_participant_ids(user):
    """Set of participant ids a member is linked to (legacy + all PDB networks)."""
    user = _authenticated(user)
    if user is None:
        return set()
    ids = set(PeeringDBNetwork.objects.filter(django_user=user)
              .values_list("participant_id", flat=True))
    ru = ring_user(user)
    if ru and ru.participant_id:
        ids.add(ru.participant_id)
    return ids


def active_participant_id(request):
    """Participant id currently in scope for a request, or None.

    Session-selected org wins; otherwise the legacy RingUser's participant,
    falling back to the first linked PeeringDB network's participant.
    """
    user = _authenticated(request.user if request is not None else None)
    if user is None:
        return None
    ids = member_participant_ids(user)
    session_pk = request.session.get("active_participant_id")
    if session_pk in ids:
        return session_pk
    ru = ring_user(user)
    if ru and ru.participant_id:
        return ru.participant_id
    link = PeeringDBNetwork.objects.filter(django_user=user).first()
    if link is not None:
        return link.participant_id
    return None


def active_participant(request):
    pid = active_participant_id(request)
    if pid is None:
        return None
    return Participant.objects.filter(pk=pid).first()


def switch_active_participant(request, participant_id):
    """Make ``participant_id`` the active org for a request, if linked."""
    if participant_id in member_participant_ids(request.user):
        request.session["active_participant_id"] = participant_id
        return True
    return False


def participant_profile(participant_or_id):
    """ParticipantProfile for a participant, or None."""
    pid = getattr(participant_or_id, "pk", participant_or_id)
    return ParticipantProfile.objects.filter(participant_id=pid).first()


def participant_autnum(participant_or_id):
    profile = participant_profile(participant_or_id)
    return profile.autnum if profile else None


def participant_for_autnum(asn):
    """Legacy Participant registered to an ASN, or None."""
    profile = ParticipantProfile.objects.filter(autnum=asn).first()
    if profile is None:
        return None
    return Participant.objects.filter(pk=profile.participant_id).first()


def set_participant_autnum(participant_or_id, asn):
    """Record/overwrite the ASN of a participant in the app-side profile."""
    pid = getattr(participant_or_id, "pk", participant_or_id)
    profile, _ = ParticipantProfile.objects.get_or_create(participant_id=pid)
    profile.autnum = asn
    profile.save()
    return profile


def participant_for_pdb(asn, net_name=None):
    """Nearest Participant for a PeeringDB network, or None.

    Resolution order:
    1. the recorded ASN identity (``ParticipantProfile.autnum``, set by
       ``ring_backfill_asn`` or on first match);
    2. a machine whose ``autnum`` equals the ASN (pre-backfill legacy data);
    3. the PeeringDB network name against ``participants.company`` — only when
       ``net_name`` is given, so callers can opt out on trust-sensitive paths
       (auto-login stays ASN-grounded; admin approval may use the name).
    """
    participant = participant_for_autnum(asn)
    if participant is None:
        pid = (
            Machine.objects.filter(autnum=asn)
            .values_list("owner__participant_id", flat=True)
            .distinct()
            .first()
        )
        if pid is not None:
            participant = Participant.objects.filter(pk=pid).first()
    if participant is None and net_name:
        participant = Participant.objects.filter(
            company__iexact=net_name.strip()
        ).first()
    return participant