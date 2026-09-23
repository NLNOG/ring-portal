"""ring_process_status: Process ansible + health data, generate report.

Port of ring-admin.py cmd_process_status / ansible_process.
"""

import time
from django.core.management.base import BaseCommand
from ring.kpi_cache import bump_kpi_version
from ring.services import nodestatus, mail
from ring.services.profiles import assert_legacy_writable


class Command(BaseCommand):
    help = "Process the ansible status data and generate a report."

    def add_arguments(self, parser):
        parser.add_argument(
            "--send", action="store_true", help="Send the report to ring-admins."
        )

    def handle(self, *args, **options):
        assert_legacy_writable()
        t0 = time.time()
        self.stdout.write("Processing status...")
        res = nodestatus.process()
        self.stdout.write("Processing done in %d seconds." % (time.time() - t0))
        bump_kpi_version()
        self.stdout.write("KPI cache invalidated.")
        for line in res.lines:
            self.stdout.write(line)
        self.stdout.write(res.report)
        if options["send"] and res.report:
            mail.send_ansible_report(res.report)
            self.stdout.write("Report sent.")
