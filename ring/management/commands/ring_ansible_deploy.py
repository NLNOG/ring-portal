"""ring_ansible_deploy: Deploy the ring-ansible repository.

Port of ring-admin.py cmd_ansible_deploy.
"""

import time
from django.core.management.base import BaseCommand, CommandError
from ring.services import deploy


class Command(BaseCommand):
    help = "Deploy the ring-ansible repository (checkout, hostfile, hostkeyfile, userfile, push)."

    def handle(self, *args, **options):
        t0 = time.time()
        self.stdout.write("Deploying ring-ansible...")
        deploy.ansible_deploy()
        self.stdout.write("Deployment done in %d seconds." % (time.time() - t0))
