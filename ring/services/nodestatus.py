from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from ring.models import AnsibleRun, HealthReport, Machine
from ring.zone import short

# Hardcoded ignore list (left empty, matching the legacy exception dict).
EXCEPTIONS = {}
INACTIVE_DAYS = 90


class StatusResult:
    def __init__(self):
        self.activated = []
        self.deactivated = []
        self.alive_v4 = []
        self.alive_v6 = []
        self.dead_v4 = []
        self.dead_v6 = []
        self.failed = {}
        self.failed_admin = {}
        self.missing_admin = []
        self.ubuntu = {}
        self.lines = []
        self.report = ""
        self.total = 0
        self.active = 0
        self.inactive = 0
        self.deactivation_skipped = False
        self.dead_v4_skipped = False
        self.dead_v6_skipped = False


def process():
    """Reconcile node status from recent ansible/health data.

    Faithful port of ring-admin.py `ansible_process()`: auto-activates and
    deactivates machines, updates alive_v4/alive_v6 from 24h health reports,
    and reports failed runs and ubuntu release distribution.
    """
    now = timezone.now()
    since = now - timedelta(days=1)
    res = StatusResult()

    machines = list(Machine.objects.select_related("owner").all())

    runs_by_host = defaultdict(list)
    for run in AnsibleRun.objects.filter(timestamp__gte=since).order_by("-timestamp"):
        runs_by_host[run.hostname].append(run)

    health4_by_host = defaultdict(list)
    health6_by_host = defaultdict(list)
    for hp in HealthReport.objects.filter(timestamp__gte=since).order_by("-timestamp"):
        target = health4_by_host if hp.family == 4 else health6_by_host
        target[hp.hostname].append(hp)

    def ubuntu_release(reports):
        if not reports:
            return None
        return reports[0].info.get("ubuntu_release", "unknown")

    for m in machines:
        hostname = m.short_hostname
        m_failed = 0
        m_seen = 0
        m_ubuntu = None
        for r in runs_by_host.get(hostname, []):
            m_seen = 1
            if r.unreachable > 0 or r.failures > 0:
                m_failed += 1
        m_seen_v4 = 0
        if health4_by_host.get(hostname):
            m_seen_v4 = 1
            m_ubuntu = ubuntu_release(health4_by_host[hostname])
        m_seen_v6 = 0
        if health6_by_host.get(hostname):
            m_seen_v6 = 1
            m_ubuntu = ubuntu_release(health6_by_host[hostname])

        if m_seen_v4 == 1 and m.alive_v4 != 1:
            res.alive_v4.append(m.hostname)
        if m_seen_v4 == 0 and m.alive_v4 != 0:
            res.dead_v4.append(m.hostname)
        if m_seen_v6 == 1 and m.alive_v6 != 1:
            res.alive_v6.append(m.hostname)
        if m_seen_v6 == 0 and m.alive_v6 != 0:
            res.dead_v6.append(m.hostname)

        if m_ubuntu:
            res.ubuntu.setdefault(m_ubuntu, []).append(m.hostname)

        if m.owner.admin == 1:
            if m_failed > 0:
                res.failed_admin[m.hostname] = m_failed
            elif m_seen == 0:
                res.missing_admin.append(m.hostname)
        else:
            res.total += 1
            if m_failed > 0:
                res.failed[m.hostname] = m_failed
            if m.active != 1:
                res.inactive += 1
                if m_seen == 1 and m_failed == 0 and m.hostname not in EXCEPTIONS:
                    res.activated.append(m.hostname)
            if m.active == 1:
                res.active += 1
                if m_seen == 0 and m_failed == 0 and m.hostname not in EXCEPTIONS:
                    res.deactivated.append(m.hostname)

    res.report = _build_report(res)
    _apply_changes(res, now)
    return res


def _build_report(res):
    report = ""
    report += "Nodes:   \t%d\n" % res.total
    report += "Active:  \t%d (%d new)\n" % (
        res.active + len(res.activated) - len(res.deactivated),
        len(res.activated),
    )
    report += "Inactive:\t%d (%d new)\n" % (
        res.inactive + len(res.deactivated) - len(res.activated),
        len(res.deactivated),
    )
    report += "Failed:  \t%d\n" % len(res.failed)
    if EXCEPTIONS:
        report += "Ignored:\t%d\n" % len(EXCEPTIONS)

    report += "\nUbuntu releases:\n"
    for version in sorted(res.ubuntu):
        report += "%s:  \t%d\n" % (version, len(res.ubuntu[version]))

    report += "\nNew nodes seen:\n"
    for a in res.activated:
        report += "  - %s\n" % a
    report += "\nNew missing nodes:\n"
    for d in res.deactivated:
        report += "  - %s\n" % d
    report += "\nFailed ansible runs:\n"
    for k, v in res.failed.items():
        report += "  - %s (%d)\n" % (k, v)
    report += "\n"
    report += "\nMissing admin nodes:\n"
    for m in res.missing_admin:
        report += "  - %s\n" % m
    report += "\nFailed runs on admin nodes:\n"
    for k, v in res.failed_admin.items():
        report += "  - %s (%d)\n" % (k, v)
    report += "\n"
    return report


def _apply_changes(res, now):
    for host in res.activated:
        m = Machine.objects.filter(hostname=host).first()
        if not m:
            res.lines.append("could not activate machine %s: not found" % host)
            continue
        _set_active(m, True, now)
        res.lines.append("machine %s marked as active" % host)
    res.report += "%d nodes activated.\n" % len(res.activated)

    if len(res.deactivated) > 10:
        res.deactivation_skipped = True
        res.report += (
            "More than 10 missing nodes since last run. Automatic deactivation skipped.\n"
        )
    else:
        for host in res.deactivated:
            m = Machine.objects.filter(hostname=host).first()
            if not m:
                res.lines.append("could not deactivate machine %s: not found" % host)
                continue
            _set_active(m, False, now)
            res.lines.append("machine %s marked as inactive" % host)
        res.report += "%d nodes deactivated.\n" % len(res.deactivated)

    for host in res.alive_v4:
        Machine.objects.filter(hostname=host).update(alive_v4=True)
    for host in res.alive_v6:
        Machine.objects.filter(hostname=host).update(alive_v6=True)

    if len(res.dead_v4) > 10:
        res.dead_v4_skipped = True
        res.report += (
            "More than 10 missing nodes on ipv4 since last run. Health update skipped.\n"
        )
        res.report += "(" + ",".join(res.dead_v4) + ")\n"
    else:
        for host in res.dead_v4:
            Machine.objects.filter(hostname=host).update(alive_v4=False)
            res.lines.append("machine %s marked as dead on IPv4" % host)
    if len(res.dead_v6) > 10:
        res.dead_v6_skipped = True
        res.report += (
            "More than 10 missing nodes on ipv6 since last run. Health update skipped.\n"
        )
        res.report += "(" + ",".join(res.dead_v6) + ")\n"
    else:
        for host in res.dead_v6:
            Machine.objects.filter(hostname=host).update(alive_v6=False)
            res.lines.append("machine %s marked as dead on IPv6" % host)


def _set_active(machine, active, now):
    Machine.objects.filter(pk=machine.pk).update(active=active, last_active=now)