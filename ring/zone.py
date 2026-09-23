import re

from django.conf import settings


def fqdn(hostname):
    """Normalize a hostname to the full unit name (append RING_ZONE)."""
    hostname = hostname.rstrip(".")
    if re.match(r".*%s$" % settings.RING_ZONE, hostname):
        return hostname
    return "%s.%s" % (hostname, settings.RING_ZONE)


def short(hostname):
    """Strip the RING_ZONE suffix to get the short node name."""
    zone = settings.RING_ZONE
    if hostname.endswith("." + zone):
        return hostname[: -(len(zone) + 1)]
    return hostname