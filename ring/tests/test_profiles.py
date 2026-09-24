"""Tests for the SQLite profile layer and the epoch timestamp field."""

import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from ring.models import Machine, Participant, RingUser, RingUserProfile
from ring.services.profiles import (
    link_ring_user,
    participant_autnum,
    participant_for_autnum,
    participant_for_pdb,
    profile_for_ring_user,
    ring_user,
    ring_user_for_pdb,
    set_participant_autnum,
)


def make_pair(username="profileuser"):
    p = Participant.objects.create(company=username + "-co")
    ru = RingUser.objects.create(username=username, participant=p, active=1)
    user = User.objects.create_user(username=username + "-d", password="pw")
    return p, ru, user


class EpochDateTimeFieldTest(TestCase):
    def test_roundtrip_validation(self):
        p, ru, _ = make_pair()
        now = timezone.now().replace(microsecond=0)
        m = Machine.objects.create(hostname="epoch.ring.nlnog.net", owner=ru, autnum=1)
        m.last_active = now
        m.save()
        m.refresh_from_db()
        self.assertEqual(m.last_active, now)

    def test_epoch_zero_reads_as_none(self):
        p, ru, _ = make_pair()
        m = Machine.objects.create(hostname="zero.ring.nlnog.net", owner=ru, autnum=1)
        # Force the integer 0 (legacy NULL sentinel) into the column.
        m.save()
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute(
                "UPDATE machines SET last_active = 0 WHERE id = %s", [m.pk]
            )
        m.refresh_from_db()
        self.assertIsNone(m.last_active)


class ProfileHelpersTest(TestCase):
    def test_link_and_resolve(self):
        p, ru, user = make_pair()
        link_ring_user(user, ru, peeringdb_id=44100, peeringdb_net_id=42)
        self.assertEqual(ring_user(user).pk, ru.pk)
        self.assertEqual(profile_for_ring_user(ru.pk).django_user.pk, user.pk)
        self.assertEqual(ring_user_for_pdb(44100)[0].pk, ru.pk)
        self.assertIsNone(ring_user_for_pdb(99999)[0])

    def test_link_single_profile_per_user(self):
        p, ru, user = make_pair()
        link_ring_user(user, ru, peeringdb_id=1)
        link_ring_user(user, ru, peeringdb_id=2)
        self.assertEqual(RingUserProfile.objects.filter(django_user=user).count(), 1)
        self.assertEqual(ring_user_for_pdb(2)[0].pk, ru.pk)
        self.assertIsNone(ring_user_for_pdb(1)[0])

    def test_ring_user_none_for_anonymous_and_unlinked(self):
        p, ru, user = make_pair()
        self.assertIsNone(ring_user(None))
        self.assertIsNone(ring_user(User.objects.get(pk=user.pk)))

    def test_participant_autnum_helpers(self):
        p, _, _ = make_pair()
        self.assertIsNone(participant_autnum(p.pk))
        set_participant_autnum(p.pk, 2914)
        self.assertEqual(participant_autnum(p.pk), 2914)
        self.assertEqual(participant_for_autnum(2914).pk, p.pk)
        self.assertIsNone(participant_for_autnum(999))

    def test_participant_for_pdb_falls_back_to_machine_asn(self):
        p, ru, _ = make_pair()
        self.assertIsNone(participant_for_pdb(2914))
        Machine.objects.create(
            hostname="pdb-asn.ring.nlnog.net", owner=ru, autnum=2914
        )
        self.assertEqual(participant_for_pdb(2914).pk, p.pk)
        self.assertIsNone(participant_for_pdb(999))

    def test_participant_for_pdb_company_lookup_is_opt_in(self):
        p, _, _ = make_pair()
        self.assertEqual(
            participant_for_pdb(999, "PROFILEUSER-co").pk, p.pk
        )
        self.assertIsNone(participant_for_pdb(999))
        self.assertIsNone(participant_for_pdb(999, "No Such Co"))


class ProfilesReadonlyGuardTest(TestCase):
    def test_writable_in_mirror_mode(self):
        from django.conf import settings

        self.assertNotIn("legacy", settings.DATABASES)
        from ring.services.profiles import legacy_writable

        self.assertTrue(legacy_writable())