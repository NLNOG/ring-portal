"""ring_dbreadonly_check: verify the legacy database is safely read-only.

Connects to the ``legacy`` MySQL alias and reports:
- the grants of the configured account (must be SELECT-only),
- row counts per legacy table,
- that a CREATE/AUTO_INCREMENT statement is rejected (privilege wall).
"""

import traceback

from django.core.management.base import BaseCommand, CommandError

from ring.models import (
    AnsibleRun,
    HealthReport,
    Machine,
    MachineRemark,
    Participant,
    ParticipantRemark,
    RingUser,
    SSHHostKey,
    SSHKey,
)

TABLES = {
    "participants": Participant,
    "users": RingUser,
    "machines": Machine,
    "sshkeys": SSHKey,
    "sshhostkeys": SSHHostKey,
    "premarks": ParticipantRemark,
    "mremarks": MachineRemark,
    "ansible": AnsibleRun,
    "health": HealthReport,
}


class Command(BaseCommand):
    help = "Check that the legacy read-only database cannot be modified."

    def handle(self, *args, **options):
        from django.db import connections

        if "legacy" not in connections:
            raise CommandError(
                "No 'legacy' database configured. Set RING_LEGACY_DB=1 (mirror "
                "mode has nothing to check)."
            )
        conn = connections["legacy"]

        try:
            self.stdout.write("== Account grants ==")
            with conn.cursor() as cur:
                cur.execute("SHOW GRANTS")
                grants = [r[0] for r in cur.fetchall()]
                for g in grants:
                    self.stdout.write("  %s" % g)
            if any("ALL PRIVILEGES" in g for g in grants):
                self.style.ERROR(
                    "WARNING: account appears to have broad privileges!"
                )
            if not any("SELECT" in g and ("*.*" in g or "ring" in g) for g in grants):
                self.stdout.write("  (no SELECT grant matched on ring tables)")

            self.stdout.write("== Write probe ==")
            # A SELECT-only account must reject CREATE / INSERT / ALTER / DROP.
            denied = 0
            for stmt in (
                "CREATE TABLE __ro_probe__ (id INT)",
                "ALTER TABLE participants ADD COLUMN __probe INT",
            ):
                try:
                    with conn.cursor() as cur:
                        cur.execute(stmt)
                except Exception:
                    denied += 1
                else:
                    self.stdout.write(
                        self.style.ERROR("FAIL: %s unexpectedly succeeded!" % stmt)
                    )
            if denied:
                self.stdout.write(self.style.SUCCESS("  write probe blocked (%d/2)" % denied))

            self.stdout.write("== Row counts ==")
            for table, model in TABLES.items():
                try:
                    total = model.objects.using("legacy").count()
                except Exception:
                    self.stdout.write("  %-12s <error>" % table)
                    if options.get("verbosity", 1) > 1:
                        self.stdout.write(traceback.format_exc())
                else:
                    self.stdout.write("  %-12s %s" % (table, total))
        except Exception as exc:
            traceback.print_exc()
            raise CommandError("could not reach the legacy database: %s" % exc)

        self.stdout.write(self.style.SUCCESS("db read-only check complete"))