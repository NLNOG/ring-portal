from django.utils import timezone
from django.test import TestCase

from ring.models import AnsibleRun, HealthReport, Machine, Participant, RingUser
from ring.services import nodestatus


def make_owner(username="owner1"):
    p = Participant.objects.create(company=username + "-co")
    return RingUser.objects.create(username=username, participant=p, userid=5000, active=1)


class NodeStatusTransitionsTest(TestCase):
    def setUp(self):
        self.owner = make_owner()

    def test_activate_new_inactive_machine(self):
        m = Machine.objects.create(
            hostname="node01.ring.nlnog.net",
            owner=self.owner,
            autnum=123,
            active=0,
            alive_v4=0,
            alive_v6=0,
        )
        now = timezone.now()
        AnsibleRun.objects.create(hostname="node01", timestamp=now - timezone.timedelta(minutes=1), unreachable=0, failures=0, ok=1, changed=0, skipped=0)
        HealthReport.objects.create(hostname="node01", timestamp=now - timezone.timedelta(minutes=1), family=4, summary={"info": {"ubuntu_release": "22.04"}, "health": {}})

        res = nodestatus.process()
        m.refresh_from_db()
        self.assertIn("node01.ring.nlnog.net", res.activated)
        self.assertEqual(m.active, True)
        self.assertEqual(m.alive_v4, True)

    def test_deactivate_missing_inactive_guard_skips(self):
        # 11 machines that were active but now have no data
        for i in range(11):
            Machine.objects.create(
                hostname="node%02d.ring.nlnog.net" % i,
                owner=self.owner,
                autnum=1,
                active=1,
                alive_v4=1,
                alive_v6=1,
            )
        res = nodestatus.process()
        self.assertEqual(res.deactivation_skipped, True)
        self.assertTrue(Machine.objects.filter(active=1).count() >= 11)

    def test_deactivate_single_missing_machine(self):
        Machine.objects.create(
            hostname="node01.ring.nlnog.net",
            owner=self.owner,
            autnum=1,
            active=1,
            alive_v4=1,
            alive_v6=1,
        )
        res = nodestatus.process()
        self.assertEqual(res.deactivation_skipped, False)
        self.assertIn("node01.ring.nlnog.net", res.deactivated)
        Machine.objects.get(hostname="node01.ring.nlnog.net").refresh_from_db()
        self.assertEqual(Machine.objects.get(hostname="node01.ring.nlnog.net").active, False)

    def test_alive_v4_update_from_health(self):
        m = Machine.objects.create(
            hostname="node01.ring.nlnog.net",
            owner=self.owner,
            autnum=1,
            active=1,
            alive_v4=1,
            alive_v6=0,
        )
        HealthReport.objects.create(
            hostname="node01",
            timestamp=timezone.now() - timezone.timedelta(minutes=1),
            family=6,
            summary={"info": {"ubuntu_release": "22.04"}, "health": {}},
        )
        res = nodestatus.process()
        m.refresh_from_db()
        self.assertIn("node01.ring.nlnog.net", res.alive_v6)
        self.assertEqual(m.alive_v6, True)

    def test_failed_run_reported_not_activated(self):
        m = Machine.objects.create(
            hostname="node01.ring.nlnog.net",
            owner=self.owner,
            autnum=1,
            active=0,
            alive_v4=0,
            alive_v6=0,
        )
        AnsibleRun.objects.create(
            hostname="node01",
            timestamp=timezone.now() - timezone.timedelta(minutes=1),
            unreachable=1,
            failures=0,
            ok=0,
            changed=0,
            skipped=0,
        )
        res = nodestatus.process()
        m.refresh_from_db()
        self.assertNotIn("node01.ring.nlnog.net", res.activated)
        self.assertEqual(m.active, False)
        self.assertIn("node01.ring.nlnog.net", res.failed)

    def test_dead_v4_guard_skips_health_update(self):
        for i in range(11):
            Machine.objects.create(
                hostname="node%02d.ring.nlnog.net" % i,
                owner=self.owner,
                autnum=1,
                active=1,
                alive_v4=1,
                alive_v6=1,
            )
        res = nodestatus.process()
        self.assertEqual(res.dead_v4_skipped, True)
