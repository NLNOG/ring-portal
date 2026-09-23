"""ring_scan_hostkeys: Scan and update SSH host keys.

Port of ring-admin.py cmd_scan_hostkeys.
"""

import os
import re
import subprocess

from django.core.management.base import BaseCommand, CommandError
from ring.models import Machine, SSHHostKey
from ring.services.profiles import assert_legacy_writable
from ring.zone import fqdn


class Command(BaseCommand):
    help = "Scan one or all hosts for SSH host keys and update the database."

    def add_arguments(self, parser):
        parser.add_argument("hostname", nargs="?", help="Optional single hostname to scan.")

    def _scan_one(self, hostname):
        hostname = fqdn(hostname)
        try:
            machine = Machine.objects.get(hostname=hostname)
        except Machine.DoesNotExist:
            raise CommandError("machine %s not found" % hostname)

        fnull = open(os.devnull, "w")
        result = ""
        for family in "-4", "-6":
            for ktype in "ecdsa", "rsa":
                try:
                    r = subprocess.check_output(
                        ["ssh-keyscan", family, "-T", "30", "-t", ktype, hostname],
                        stderr=fnull,
                    )
                    if r:
                        result += r.decode("utf-8")
                except subprocess.CalledProcessError:
                    pass
                except Exception:
                    self.stderr.write(
                        "could not scan keys for %s (%s/%s)" % (hostname, family, ktype)
                    )

        if result:
            oldkeys = list(SSHHostKey.objects.filter(machine=machine))
            newkeys = []
            for line in result.splitlines():
                if re.search(r"^#", line):
                    continue
                vals = line.split()
                if len(vals) < 3:
                    continue
                newkeys.append({"keytype": vals[1], "sshkey": vals[2]})

            seen = []
            for old in oldkeys:
                for new in newkeys:
                    if old.keytype == new["keytype"]:
                        seen.append(old.keytype)
                        if old.sshkey != new["sshkey"]:
                            old.sshkey = new["sshkey"]
                            old.save()
                            self.stdout.write(
                                "Replaced %s key %s for %s" % (old.keytype, old.pk, hostname)
                            )
                if old.keytype not in seen:
                    old.delete()
                    self.stdout.write(
                        "Deleted %s key %s for %s" % (old.keytype, old.pk, hostname)
                    )

            for new in newkeys:
                if new["keytype"] not in seen:
                    k = SSHHostKey.objects.create(
                        machine=machine, keytype=new["keytype"], sshkey=new["sshkey"]
                    )
                    self.stdout.write(
                        "Added %s key %s for machine %s" % (new["keytype"], k.pk, hostname)
                    )

    def handle(self, *args, **options):
        assert_legacy_writable()
        hostname = options["hostname"]
        if hostname:
            self._scan_one(hostname)
        else:
            for m in Machine.objects.filter(active=1):
                self._scan_one(m.hostname)
