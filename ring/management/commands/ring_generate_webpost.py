"""ring_generate_webpost: Generate web post content for a new participant.

Port of ring-admin.py cmd_generate_webpost / cmd_generate_hugopost.
"""

from django.core.management.base import BaseCommand, CommandError
from ring.services import webpost


class Command(BaseCommand):
    help = "Generate a Hugo markdown post body for a participant joining the RING."

    def add_arguments(self, parser):
        parser.add_argument("username", help="RING username of the participant.")
        parser.add_argument(
            "--publish",
            action="store_true",
            help="Publish the generated post to the ring-web repository.",
        )

    def handle(self, *args, **options):
        try:
            webpost.generate_hugopost(
                options["username"], publish=options["publish"]
            )
        except LookupError as e:
            raise CommandError(str(e))
