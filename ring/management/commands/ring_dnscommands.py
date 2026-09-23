"""ring_dnscommands: Generate DNS (ring-pdns) commands for a machine.

Port of ring-admin.py cmd_dnscommands.
"""

from django.core.management.base import BaseCommand, CommandError
from ring.models import Machine
from ring.zone import fqdn


class Command(BaseCommand):
    help = "Generate ring-pdns commands for a machine."

    def add_arguments(self, parser):
        parser.add_argument("hostname", help="Hostname (short or FQDN).")

    def handle(self, *args, **options):
        hostname = fqdn(options["hostname"])
        try:
            m = Machine.objects.get(hostname=hostname)
        except Machine.DoesNotExist:
            raise CommandError("machine %s not found" % hostname)
        self.stdout.write("ring-pdns add node %s %s %s %s" % (m.hostname, m.v4, m.v6, m.geo))
        self.stdout.write("ring-pdns activate node %s %s" % (m.hostname, m.country))
