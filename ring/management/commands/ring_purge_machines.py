"""ring_purge_machines: Remove inactive machines past the retention window.

Port of ring-admin.py purge_inactive_machines.
"""

import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from ring.models import Machine, RingUser
from ring.services import mail
from ring.services.profiles import assert_legacy_writable


class Command(BaseCommand):
    help = "Purge machines inactive for INACTIVE_DAYS days and their data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=90, help="Inactivity window in days (default 90)."
        )

    def _deactivate_participant(self, partid):
        for u in RingUser.objects.filter(participant=partid):
            u.active = 0
            u.save()
            self.stdout.write("Deactivated user %s" % u.username)

    def handle(self, *args, **options):
        assert_legacy_writable()
        tstamp = time.time()
        days = options["days"]
        cutoff = timezone.now() - timedelta(days=days)
        machine_delete = []
        participant_deactivate = []

        machines = Machine.objects.filter(active=0).select_related("owner")
        for m in machines:
            if m.owner.admin == 1:
                continue
            if m.last_active is None:
                self.stdout.write("Not deleting %s (no last_active date)" % m.hostname)
                continue
            if m.last_active.timestamp() >= tstamp - timedelta(days=days).total_seconds():
                self.stdout.write("Not yet deleting %s" % m.hostname)
                continue

            machine_delete.append(m.hostname)
            self.stdout.write("Deleting machine %s" % m.hostname)
            mail.generate_removemail(m.hostname, send=1)

            for hk in m.hostkeys.all():
                self.stdout.write(str(hk))
            m.hostkeys.all().delete()
            self.stdout.write("ssh hostkeys for machine %s deleted" % m.hostname)

            for r in m.remarks.all():
                self.stdout.write(str(r))
            m.remarks.all().delete()
            self.stdout.write("remarks for machine %s deleted" % m.hostname)

            for row in Machine.objects.filter(hostname=m.hostname):
                self.stdout.write(str(row))
            m.delete()
            self.stdout.write("machine %s deleted" % m.hostname)

            p = m.owner.participant
            if not Machine.objects.filter(owner__participant=p).exists():
                participant_deactivate.append(p.company)
                self.stdout.write("Deactivating users for participant %s" % p.company)
                self._deactivate_participant(p.pk)
