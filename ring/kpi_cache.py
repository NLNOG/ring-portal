"""TTL caching for the expensive dashboard KPIs.

The defaults cache is Django's in-process LocMemCache, so entries are
keyed by a *version* (bumped by management commands that change the
underlying data, e.g. ring_process_status) and an hourly time bucket so a
stale window is never served for more than the KPI_TTL duration.
"""

from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

from ring.models import AnsibleRun, HealthReport, Machine
from ring.zone import short

KPI_TTL = 10 * 60
KPI_PREFIX = "ring:kpi"
KPI_VERSION_KEY = KPI_PREFIX + ":version"

FAILED_7D_NAME = "failed_7d"
UBUNTU_RELEASES_NAME = "ubuntu_releases"
UPDATE_ALERTS_NAME = "update_alerts"

MAX_FAILED = 15
MAX_UBUNTU_CUTOFF = 20000


def get_kpi_version():
    version = cache.get(KPI_VERSION_KEY)
    if version is None:
        version = 1
        cache.set(KPI_VERSION_KEY, version, None)
    return version


def bump_kpi_version():
    """Invalidate every KPI cache entry by advancing the version number.

    Call after status processing (ring_process_status) changes the data
    behind the cached aggregates.
    """
    value = get_kpi_version() + 1
    cache.set(KPI_VERSION_KEY, value, None)
    return value


def _time_bucket():
    return timezone.now().strftime("%Y%m%d%H")


def _kpi_key(name, bucket):
    return "%s:%s:%s:%s" % (KPI_PREFIX, name, get_kpi_version(), bucket)


def _cached(name, compute):
    bucket = _time_bucket()
    key = _kpi_key(name, bucket)
    value = cache.get(key)
    if value is None:
        value = compute()
        cache.set(key, value, KPI_TTL)
    return value


def cached_failed_7d(since=None):
    """Hosts with unreachable/failure ansible runs in the last 7 days."""
    since = since if since is not None else timezone.now() - timedelta(days=7)

    def compute():
        return list(
            AnsibleRun.objects.filter(timestamp__gte=since)
            .filter(Q(unreachable__gt=0) | Q(failures__gt=0))
            .values("hostname")
            .annotate(count=Count("id"))
            .order_by("-count")[:MAX_FAILED]
        )

    return _cached(FAILED_7D_NAME, compute)


def cached_ubuntu_releases(since=None):
    """Ubuntu release distribution across machines with fresh health data.

    Returns (releases, releases_by_short, short2fqdn):
      * releases         - {release_label: count} across the ring
      * releases_by_short- {short_hostname: release_label} only for hostnames
                            that exist in the machines table
      * short2fqdn       - {short_hostname: fqdn} lookup for every machine
    """
    since = since if since is not None else timezone.now() - timedelta(days=7)

    def compute():
        short2fqdn = {
            short(m.hostname): m.hostname
            for m in Machine.objects.only("hostname")
        }
        latest = {}
        for hp in (
            HealthReport.objects.filter(timestamp__gte=since, hostname__isnull=False)
            .order_by("-timestamp")
            .only("hostname", "timestamp", "summary")
            .iterator()
        ):
            if hp.hostname not in short2fqdn:
                continue
            latest.setdefault(hp.hostname, hp)
        releases = {}
        releases_by_short = {}
        for hn, hp in latest.items():
            rel = hp.info.get("ubuntu_release") or "unknown"
            releases[rel] = releases.get(rel, 0) + 1
            releases_by_short[hn] = rel
        releases = dict(sorted(releases.items()))
        return releases, releases_by_short, short2fqdn

    return _cached(UBUNTU_RELEASES_NAME, compute)


def cached_update_alerts(since=None):
    """Latest per-host reboot/package-update flags from recent health data.

    Returns {short_hostname: {fqdn, needs_reboot, updates,
    security_updates}} for every hostname that exists in the machines table,
    using the latest health report (across both families) within the window.
    Hosts with no fresh health data get no entry.
    """
    since = since if since is not None else timezone.now() - timedelta(days=7)

    def compute():
        short2fqdn = {
            short(m.hostname): m.hostname
            for m in Machine.objects.only("hostname")
        }
        latest = {}
        for hp in (
            HealthReport.objects.filter(timestamp__gte=since, hostname__isnull=False)
            .order_by("-timestamp")
            .only("hostname", "timestamp", "summary")
            .iterator()
        ):
            if hp.hostname not in short2fqdn:
                continue
            latest.setdefault(hp.hostname, hp)
        return {
            hn: {
                "fqdn": short2fqdn[hn],
                "needs_reboot": bool(hp.info.get("needs_reboot")),
                "updates": int(hp.info.get("ubuntu_updates") or 0),
                "security_updates": int(hp.info.get("ubuntu_security_updates") or 0),
            }
            for hn, hp in latest.items()
        }

    return _cached(UPDATE_ALERTS_NAME, compute)