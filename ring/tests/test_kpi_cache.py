from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from ring.kpi_cache import (
    bump_kpi_version,
    cached_failed_7d,
    cached_ubuntu_releases,
    cached_update_alerts,
    get_kpi_version,
)
from ring.models import AnsibleRun, HealthReport, Machine, Participant, RingUser


def make_machine(hostname="node01.ring.nlnog.net", **kw):
    short_name = hostname.split(".")[0]
    p = Participant.objects.create(company="co-" + short_name)
    owner = RingUser.objects.create(
        username="owner-" + short_name, participant=p, userid=1, active=1
    )
    defaults = dict(hostname=hostname, owner=owner, autnum=123, active=1)
    defaults.update(kw)
    return Machine.objects.create(**defaults)


class KpiCacheTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_failed_7d(self):
        now = timezone.now()
        since = now - timezone.timedelta(days=7)
        AnsibleRun.objects.create(
            hostname="node01", timestamp=now - timezone.timedelta(hours=1),
            failures=1, unreachable=0, ok=0, changed=0, skipped=0,
        )
        AnsibleRun.objects.create(
            hostname="node01", timestamp=now - timezone.timedelta(hours=2),
            failures=1, unreachable=0, ok=0, changed=0, skipped=0,
        )
        AnsibleRun.objects.create(
            hostname="node02", timestamp=now - timezone.timedelta(hours=3),
            failures=0, unreachable=2, ok=0, changed=0, skipped=0,
        )
        AnsibleRun.objects.create(
            hostname="node03", timestamp=now - timezone.timedelta(hours=4),
            failures=0, unreachable=0, ok=1, changed=0, skipped=0,
        )
        hosts = {r["hostname"]: r["count"] for r in cached_failed_7d(since)}
        self.assertEqual(hosts, {"node01": 2, "node02": 1})
        with self.assertNumQueries(0):
            cached_failed_7d(since)

    def test_failed_7d_invalidated_by_version_bump(self):
        now = timezone.now()
        since = now - timezone.timedelta(days=7)
        AnsibleRun.objects.create(
            hostname="node01", timestamp=now, failures=1, unreachable=0,
            ok=0, changed=0, skipped=0,
        )
        cached_failed_7d(since)
        bump_kpi_version()
        self.assertGreater(get_kpi_version(), 1)
        AnsibleRun.objects.create(
            hostname="node02", timestamp=now, failures=1, unreachable=0,
            ok=0, changed=0, skipped=0,
        )
        hosts = {r["hostname"]: r["count"] for r in cached_failed_7d(since)}
        self.assertEqual(hosts, {"node01": 1, "node02": 1})

    def test_ubuntu_releases(self):
        make_machine("node01.ring.nlnog.net")
        make_machine("node02.ring.nlnog.net")
        now = timezone.now()
        since = now - timezone.timedelta(days=7)
        HealthReport.objects.create(
            hostname="node01", timestamp=now, family=4,
            summary={"info": {"ubuntu_release": "22.04"}, "health": {}},
        )
        HealthReport.objects.create(
            hostname="node01", timestamp=now - timezone.timedelta(hours=1), family=4,
            summary={"info": {"ubuntu_release": "20.04"}, "health": {}},
        )
        HealthReport.objects.create(
            hostname="node02", timestamp=now, family=4,
            summary={"info": {"ubuntu_release": "22.04"}, "health": {}},
        )
        HealthReport.objects.create(
            hostname="ghost", timestamp=now, family=4,
            summary={"info": {"ubuntu_release": "18.04"}, "health": {}},
        )
        releases, by_short, short2fqdn = cached_ubuntu_releases(since)
        self.assertEqual(releases, {"22.04": 2})
        self.assertEqual(by_short, {"node01": "22.04", "node02": "22.04"})
        self.assertNotIn("ghost", by_short)
        self.assertEqual(
            short2fqdn,
            {
                "node01": "node01.ring.nlnog.net",
                "node02": "node02.ring.nlnog.net",
            },
        )
        with self.assertNumQueries(0):
            cached_ubuntu_releases(since)

    def test_ubuntu_releases_invalidated_by_version_bump(self):
        make_machine("node01.ring.nlnog.net")
        now = timezone.now()
        since = now - timezone.timedelta(days=7)
        HealthReport.objects.create(
            hostname="node01", timestamp=now, family=4,
            summary={"info": {"ubuntu_release": "22.04"}, "health": {}},
        )
        releases, _, _ = cached_ubuntu_releases(since)
        self.assertEqual(releases.get("22.04"), 1)
        bump_kpi_version()
        HealthReport.objects.create(
            hostname="node01", timestamp=now + timezone.timedelta(minutes=1), family=6,
            summary={"info": {"ubuntu_release": "24.04"}, "health": {}},
        )
        releases, _, _ = cached_ubuntu_releases(since)
        self.assertEqual(releases, {"24.04": 1})

    def test_update_alerts(self):
        make_machine("node01.ring.nlnog.net")
        make_machine("node02.ring.nlnog.net")
        now = timezone.now()
        HealthReport.objects.create(
            hostname="node01", timestamp=now - timezone.timedelta(hours=1), family=6,
            summary={"info": {"needs_reboot": False}, "health": {}},
        )
        HealthReport.objects.create(
            hostname="node01", timestamp=now, family=4,
            summary={
                "info": {
                    "needs_reboot": True,
                    "ubuntu_updates": 12,
                    "ubuntu_security_updates": 3,
                },
                "health": {},
            },
        )
        HealthReport.objects.create(
            hostname="node02", timestamp=now, family=4,
            summary={"info": {}, "health": {}},
        )
        # unknown host is ignored
        HealthReport.objects.create(
            hostname="ghost", timestamp=now, family=4,
            summary={"info": {"needs_reboot": True}, "health": {}},
        )
        alerts = cached_update_alerts(since=now - timezone.timedelta(days=7))
        self.assertEqual(
            alerts["node01"],
            {
                "fqdn": "node01.ring.nlnog.net",
                "needs_reboot": True,
                "updates": 12,
                "security_updates": 3,
            },
        )
        self.assertEqual(alerts["node02"]["fqdn"], "node02.ring.nlnog.net")
        self.assertIs(alerts["node02"]["needs_reboot"], False)
        self.assertEqual(alerts["node02"]["updates"], 0)
        self.assertNotIn("ghost", alerts)
        with self.assertNumQueries(0):
            cached_update_alerts(since=now - timezone.timedelta(days=7))

    def test_update_alerts_invalidated_by_version_bump(self):
        make_machine("node01.ring.nlnog.net")
        now = timezone.now()
        since = now - timezone.timedelta(days=7)
        HealthReport.objects.create(
            hostname="node01", timestamp=now, family=4,
            summary={"info": {"needs_reboot": True}, "health": {}},
        )
        cached_update_alerts(since)
        bump_kpi_version()
        HealthReport.objects.create(
            hostname="node01", timestamp=now + timezone.timedelta(minutes=1), family=4,
            summary={"info": {"needs_reboot": False, "ubuntu_updates": 5}, "health": {}},
        )
        alerts = cached_update_alerts(since)
        self.assertIs(alerts["node01"]["needs_reboot"], False)
        self.assertEqual(alerts["node01"]["updates"], 5)