"""ring_generate_mail: Generate/send ring notification emails.

Port of ring-admin.py cmd_generate_*mail / cmd_send_*mail.
"""

from django.core.management.base import BaseCommand, CommandError
from ring.services import mail


class Command(BaseCommand):
    help = "Generate (and optionally send) ring notification emails."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="kind", required=True)

        def add_node_cmd(name, func):
            p = sub.add_parser(name)
            p.add_argument("node", help="Hostname (short or FQDN).")
            p.add_argument("--send", action="store_true")
            return p

        p = sub.add_parser("welcome")
        p.add_argument("username")
        p.add_argument("--send", action="store_true")

        p = sub.add_parser("announce")
        p.add_argument("username")
        p.add_argument("--send", action="store_true")

        add_node_cmd("down", "generate_downmail")
        add_node_cmd("remove", "generate_removemail")
        add_node_cmd("failedupgrade", "generate_failedupgrademail")
        add_node_cmd("cannotupgrade", "generate_cannotupgrademail")
        add_node_cmd("disk", "generate_diskmail")

        p = sub.add_parser("downreminders")
        p.add_argument("--send", action="store_true")

    def handle(self, *args, **options):
        kind = options["kind"]
        send = bool(options.get("send"))
        try:
            if kind == "welcome":
                mail.generate_welcomemail(options["username"], send=send)
            elif kind == "announce":
                mail.generate_announcemail(options["username"], send=send)
            elif kind == "downreminders":
                mail.generate_downreminders(send=send)
            else:
                getattr(mail, "generate_" + kind)(options.get("node"), send=send)
        except mail.RingMailError as e:
            raise CommandError(str(e))
