"""Dashboard views for the RING domain."""

import json
import re
import secrets
import urllib.request
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from rest_framework.authtoken.models import Token

from ring.context_processors import user_can_manage
from ring.kpi_cache import (
    cached_failed_7d,
    cached_ubuntu_releases,
    cached_update_alerts,
)
from ring.models import (
    AnsibleRun,
    HealthReport,
    Machine,
    Participant,
    PeeringDBSignup,
    RingSignup,
    RingUser,
)
from ring.services.peeringdb import (
    PDBError,
    authorize_url,
    exchange_code,
    fetch_profile,
)
from ring.services.profiles import (
    link_ring_user,
    participant_for_pdb,
    ring_user,
    ring_user_for_pdb,
    set_participant_autnum,
)
from ring.zone import fqdn, short

# Static fallback for the node health-check descriptions, matching the
# "descriptions" block of a live node's status.json endpoint.
HEALTH_DESCRIPTIONS = {
    "mountstatus_root": "The root filesystem is in read/write status",
    "diskspace_root": "The root filesystem has enough free disk space",
    "diskspace_boot": "The boot filesystem has enough free disk space",
    "ipv6_addresses": "The IPv6 address of the node matches the ring database",
    "ipv6_gateway": "The IPv6 gateway is reachable",
    "ipv6_reachability": "There is IPv6 connectivity beyond the gateway",
    "ipv4_addresses": "The IPv4 address of the node matches the ring database",
    "ipv4_gateway": "The IPv4 gateway is reachable",
    "ipv4_reachability": "There is IPv4 connectivity beyond the gateway",
    "dns_config": "The local host is configured as DNS resolver",
    "dns_resolvers": "The configured DNS resolvers are functioning",
    "ntp_status": "NTP is running and the clock is synchronized",
    "sshd_status": "The SSH daemon is running",
    "https_github": "A webrequest for 'https://github.com/' succeeded",
    "http_aptrepo_bit": "A webrequest for 'http://nl.archive.ubuntu.com/ubuntu' succeeded",
    "http_aptrepo_ring": "A webrequest for 'http://apt.ring.nlnog.net/deb/dists/ring/Release' succeeded",
    "ansible_cron": "The ansible cron job is correctly configured",
    "ansible_run": "Ansible has recently run",
    "ipv4_ringapi": "Pushing this health report to the IPv4 ring API (https://95.211.149.25/) succeeded",
    "ipv6_ringapi": "Pushing this health report to the IPv6 ring API (https://[2001:1af8:4013::25]/) succeeded",
}


def _fetch_status(hostname):
    """Best-effort fetch of a node's status.json (full payload)."""
    try:
        with urllib.request.urlopen(
            "http://%s/status.json" % hostname, timeout=3
        ) as r:
            return json.load(r)
    except Exception:
        return None


def country_flag(country_code):
    """Map an ISO 3166-1 alpha-2 code to its regional indicator flag emoji."""
    code = (country_code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(ord(c) + 0x1F1E6 - ord("A")) for c in code)


def index(request):
    if not request.user.is_authenticated:
        return render(request, "ring/login_required.html")
    now = timezone.now()
    since = now - timedelta(days=7)

    total = Machine.objects.count()
    active = Machine.objects.filter(active=True).count()
    inactive = total - active
    participants = Participant.objects.count()

    ubuntu_releases, _, _ = cached_ubuntu_releases()
    ubuntu_items = sorted(ubuntu_releases.items())

    connectivity_qs = (
        Machine.objects.filter(active=True)
        .filter(Q(alive_v4=False) | Q(alive_v6=False))
        .select_related("owner")
    )
    dead_v4 = connectivity_qs.filter(alive_v4=False).count()
    dead_v6 = connectivity_qs.filter(alive_v6=False).count()

    update_alerts = cached_update_alerts()

    machines_map = {
        m.hostname: m for m in Machine.objects.select_related("owner")
    }
    short2fqdn = {short(fqdn): fqdn for fqdn in machines_map}

    issues = {}

    def mark(fqdn, issue_type):
        cell = issues.setdefault(
            fqdn,
            {
                "fqdn": fqdn,
                "short": short(fqdn),
                "m": machines_map.get(fqdn),
                "types": set(),
                "needs_reboot": False,
                "updates": 0,
                "security_updates": 0,
                "failures": 0,
            },
        )
        cell["types"].add(issue_type)
        return cell

    for m in connectivity_qs:
        mark(m.hostname, "connectivity")

    for a in update_alerts.values():
        if a["needs_reboot"] or a["updates"] or a["security_updates"]:
            cell = mark(a["fqdn"], "updates")
            cell["needs_reboot"] = a["needs_reboot"]
            cell["updates"] = a["updates"]
            cell["security_updates"] = a["security_updates"]
            if a["needs_reboot"]:
                mark(a["fqdn"], "reboot")

    for f in cached_failed_7d(since):
        fqdn = short2fqdn.get(f["hostname"])
        if fqdn:
            cell = mark(fqdn, "ansible_failed")
            cell["failures"] = f["count"]

    for m in Machine.objects.exclude(active=True).select_related("owner"):
        mark(m.hostname, "inactive")

    issue_type = request.GET.get("issues", "all")
    if issue_type not in (
        "connectivity", "updates", "reboot", "ansible_failed", "inactive"
    ):
        issue_type = "all"
    issue_rows = sorted(issues.values(), key=lambda c: c["fqdn"])
    if issue_type != "all":
        issue_rows = [c for c in issue_rows if issue_type in c["types"]]
    type_counts = {
        t: sum(1 for c in issues.values() if t in c["types"])
        for t in ("connectivity", "updates", "reboot", "ansible_failed", "inactive")
    }
    need_reboot = sum(1 for c in issues.values() if c["needs_reboot"])
    updates_pending = sum(1 for c in issues.values() if c["updates"])
    security_updates = sum(1 for c in issues.values() if c["security_updates"])

    return render(
        request,
        "ring/index.html",
        {
            "total": total,
            "active": active,
            "inactive": inactive,
            "participants": participants,
            "ubuntu_items": ubuntu_items,
            "ubuntu_labels": ["%s (%s)" % (k, v) for k, v in ubuntu_items],
            "ubuntu_values": [v for _, v in ubuntu_items],
            "dead_v4": dead_v4,
            "dead_v6": dead_v6,
            "issue_rows": issue_rows,
            "issue_type": issue_type,
            "type_counts": type_counts,
            "need_reboot": need_reboot,
            "updates_pending": updates_pending,
            "security_updates": security_updates,
        },
    )


@login_required
def machines(request):
    qs = Machine.objects.select_related("owner__participant")
    country = request.GET.get("country", "")
    state = request.GET.get("state", "")
    status = request.GET.get("status", "")
    ipv4 = request.GET.get("ipv4", "")
    ipv6 = request.GET.get("ipv6", "")
    ubuntu = request.GET.get("ubuntu", "")
    q = request.GET.get("q", "")
    participant = request.GET.get("participant", "")

    participant_pk = None
    if participant:
        try:
            participant_pk = int(participant)
            qs = qs.filter(owner__participant_id=participant_pk)
        except ValueError:
            participant_pk = None

    owner_pk = None
    if request.GET.get("owner"):
        try:
            owner_pk = int(request.GET.get("owner"))
            qs = qs.filter(owner_id=owner_pk)
        except ValueError:
            owner_pk = None

    if country:
        qs = qs.filter(country__iexact=country[:2])
    if state:
        qs = qs.filter(state__iexact=state[:2])
    if status == "active":
        qs = qs.filter(active=True)
    elif status == "inactive":
        qs = qs.exclude(active=True)
    if ipv4 == "up":
        qs = qs.filter(alive_v4=True)
    elif ipv4 == "down":
        qs = qs.filter(alive_v4=False)
    if ipv6 == "up":
        qs = qs.filter(alive_v6=True)
    elif ipv6 == "down":
        qs = qs.filter(alive_v6=False)
    if ubuntu:
        _, releases_by_short, short2fqdn = cached_ubuntu_releases()
        matching = {
            short2fqdn[hn]
            for hn, rel in releases_by_short.items()
            if rel == ubuntu
        }
        qs = qs.filter(hostname__in=matching) if matching else qs.none()
    if q:
        qs = qs.filter(Q(hostname__icontains=q) | Q(city__icontains=q))

    countries = (
        Machine.objects.exclude(country__isnull=True)
        .values_list("country", flat=True)
        .distinct()
        .order_by("country")
    )

    ubuntu_releases, _, _ = cached_ubuntu_releases()
    ubuntu_options = sorted(ubuntu_releases)

    participant_company = ""
    if participant_pk:
        participant_company = (
            Participant.objects.filter(pk=participant_pk)
            .values_list("company", flat=True)
            .first()
            or ""
        )

    owner_username = ""
    if owner_pk:
        owner_username = (
            RingUser.objects.filter(pk=owner_pk)
            .values_list("username", flat=True)
            .first()
            or ""
        )

    return render(
        request,
        "ring/machines.html",
        {
            "machines": qs,
            "countries": countries,
            "ubuntu_options": ubuntu_options,
            "country": country,
            "state": state,
            "status": status,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "ubuntu": ubuntu,
            "q": q,
            "participant": participant_pk or "",
            "participant_company": participant_company,
            "owner": owner_pk or "",
            "owner_username": owner_username,
        },
    )


def _parse_geo(geo):
    if not geo:
        return None
    try:
        lat, lon = (float(x.strip()) for x in geo.split(","))
    except (ValueError, AttributeError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _geo_map(geo):
    coords = _parse_geo(geo)
    if not coords:
        return None
    lat, lon = coords
    d = 0.03
    return {
        "lat": lat,
        "lon": lon,
        "bbox": "%(minlon).6f,%(minlat).6f,%(maxlon).6f,%(maxlat).6f"
        % {
            "minlon": lon - d,
            "minlat": lat - d,
            "maxlon": lon + d,
            "maxlat": lat + d,
        },
    }


@login_required
def machine_detail(request, hostname):
    hostname = fqdn(hostname)
    machine = get_object_or_404(
        Machine.objects.select_related("owner__participant"), hostname=hostname
    )
    hostkeys = machine.hostkeys.all()
    remarks = machine.remarks.all()

    has_v4 = bool(machine.v4)

    def strip_ipv4_checks(h):
        if has_v4 or not h.summary or not isinstance(h.summary, dict):
            return h
        summary = dict(h.summary)
        health = summary.get("health") or {}
        if isinstance(health, dict):
            summary["health"] = {
                k: v for k, v in health.items() if not k.lower().startswith("ipv4_")
            }
        h.summary = summary
        return h

    health_filter = "failed" if request.GET.get("health") == "failed" else ""
    runs_filter = "failed" if request.GET.get("runs") == "failed" else ""

    health_qs = HealthReport.objects.filter(hostname=short(hostname)).order_by("-timestamp")
    latest_health = health_qs.first()
    if health_filter == "failed":
        recent_health = []
        for h in health_qs[:200]:
            h = strip_ipv4_checks(h)
            if h.failure_checks or h.info.get("success") is False:
                recent_health.append(h)
                if len(recent_health) == 12:
                    break
    else:
        recent_health = [strip_ipv4_checks(h) for h in health_qs[:12]]

    runs_qs = AnsibleRun.objects.filter(hostname=short(hostname))
    if runs_filter == "failed":
        runs_qs = runs_qs.filter(
            Q(changed__gt=0) | Q(failures__gt=0) | Q(unreachable__gt=0)
        )
    recent_runs = runs_qs[:10]

    descriptions = HEALTH_DESCRIPTIONS
    map_data = _geo_map(machine.geo)

    return render(
        request,
        "ring/machine_detail.html",
        {
            "machine": machine,
            "latest_health": latest_health,
            "hostkeys": hostkeys,
            "remarks": remarks,
            "recent_health": recent_health,
            "recent_runs": recent_runs,
            "health_filter": health_filter,
            "runs_filter": runs_filter,
            "health_qs": "health=failed" if health_filter == "failed" else "",
            "runs_qs": "runs=failed" if runs_filter == "failed" else "",
            "descriptions": descriptions,
            "flag": country_flag(machine.country),
            "map_data": map_data,
        },
    )


@login_required
def machine_status(request, hostname):
    hostname = fqdn(hostname)
    machine = get_object_or_404(Machine, hostname=hostname)
    data = _fetch_status(machine.hostname)
    if data is None:
        return JsonResponse(
            {"error": "could not retrieve live status for %s" % machine.hostname},
            status=502,
        )
    return JsonResponse(data)


@login_required
def participant_info(request, pk):
    participant = get_object_or_404(Participant, pk=pk)
    data = {"id": participant.pk, "company": participant.company}
    for name in ("url", "contact", "email", "nocemail", "companydesc", "public"):
        data[name] = getattr(participant, name)
    data["tstamp"] = (
        participant.tstamp.isoformat() if participant.tstamp else None
    )
    data["machine_count"] = participant.users.aggregate(
        n=Count("machines", distinct=True)
    )["n"]
    nodes = []
    for m in Machine.objects.filter(owner__participant=participant).order_by(
        "hostname"
    ).only("hostname", "city", "country", "autnum", "alive_v4", "alive_v6", "geo"):
        coords = _parse_geo(m.geo)
        nodes.append(
            {
                "hostname": m.hostname,
                "short_hostname": short(m.hostname),
                "city": m.city,
                "country": m.country,
                "flag": country_flag(m.country),
                "asn": m.autnum,
                "alive_v4": bool(m.alive_v4),
                "alive_v6": bool(m.alive_v6),
                "lat": coords[0] if coords else None,
                "lon": coords[1] if coords else None,
            }
        )
    data["nodes"] = nodes
    return JsonResponse(data)


@login_required
def users(request):
    qs = RingUser.objects.select_related("participant").annotate(
        machine_count=Count("machines", distinct=True)
    ).order_by("username")
    q = request.GET.get("q", "")
    status = request.GET.get("status", "")
    if status == "active":
        qs = qs.filter(active=True)
    elif status == "inactive":
        qs = qs.exclude(active=True)
    if q:
        qs = qs.filter(
            Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(participant__company__icontains=q)
        )
    return render(
        request,
        "ring/users.html",
        {"users": qs, "q": q, "status": status},
    )


@login_required
def participants(request):
    participants = Participant.objects.annotate(
        machine_count=Count("users__machines", distinct=True)
    ).order_by("company")
    q = request.GET.get("q", "").strip()
    if request.GET.get("machines") == "yes":
        participants = participants.exclude(machine_count=0)
    if q:
        participants = participants.filter(
            Q(company__icontains=q)
            | Q(contact__icontains=q)
            | Q(email__icontains=q)
            | Q(nocemail__icontains=q)
        )
    return render(
        request,
        "ring/participants.html",
        {
            "participants": participants,
            "machines": "yes" if request.GET.get("machines") == "yes" else "",
            "auto_pk": request.GET.get("p", ""),
            "editable_pks": _editable_participant_pks(request.user),
            "q": q,
        },
    )


def _editable_participant_pks(user):
    """Set of participant pks a user may edit, or None meaning "all"."""
    if user_can_manage(user):
        return None
    ru = ring_user(user)
    if ru and ru.participant_id:
        return {ru.participant_id}
    return set()


class ParticipantEditForm(forms.ModelForm):
    class Meta:
        model = Participant
        fields = ["company", "contact", "email", "nocemail", "url", "companydesc", "public"]
        widgets = {
            "company": forms.TextInput(attrs={"class": "form-input"}),
            "contact": forms.TextInput(attrs={"class": "form-input"}),
            "email": forms.EmailInput(attrs={"class": "form-input"}),
            "nocemail": forms.EmailInput(attrs={"class": "form-input"}),
            "url": forms.URLInput(attrs={"class": "form-input"}),
            "companydesc": forms.Textarea(attrs={"class": "form-input", "rows": 4}),
        }

    public = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check"}),
        label="Public participant",
    )

    def save(self, commit=True):
        participant = super().save(commit=False)
        participant.public = self.cleaned_data.get("public") or None
        if commit:
            participant.save()
        return participant


@login_required
def participant_edit(request, pk):
    participant = get_object_or_404(Participant, pk=pk)
    ru = ring_user(request.user)
    if not user_can_manage(request.user) and not (
        ru and ru.participant_id == participant.pk
    ):
        return redirect("ring-login")
    can_rename = user_can_manage(request.user)

    if request.method == "POST":
        form = ParticipantEditForm(request.POST, instance=participant)
        if not can_rename:
            form.fields.pop("company")
        if form.is_valid():
            form.save()
            messages.success(
                request, "Organisation %s updated." % participant.company
            )
            return redirect("ring-participants")
    else:
        form = ParticipantEditForm(instance=participant)
        if not can_rename:
            form.fields.pop("company")
    return render(
        request,
        "ring/participants_edit.html",
        {"form": form, "participant": participant, "can_rename": can_rename},
    )


def signup(request):
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""
        password2 = request.POST.get("password2") or ""
        company_id = request.POST.get("company") or ""
        ring_username = (request.POST.get("ring_username") or "").strip()
        notes = (request.POST.get("notes") or "").strip()

        if get_user_model().objects.filter(username=username).exists():
            messages.error(request, "That username is already taken.")
        elif password and password != password2:
            messages.error(request, "Passwords do not match.")
        elif not (username and email and password):
            messages.error(request, "Username, email and password are required.")
        elif not company_id.isdigit():
            messages.error(request, "Please select the organisation you belong to.")
        else:
            try:
                company = Participant.objects.get(pk=company_id)
            except (Participant.DoesNotExist, ValueError):
                company = None
            if company is None:
                messages.error(request, "Please select a valid organisation.")
            else:
                user = get_user_model().objects.create_user(
                    username=username, email=email, password=password
                )
                user.is_active = False
                user.save()
                RingSignup.objects.create(
                    django_user=user,
                    participant_id=company.pk,
                    ring_username=ring_username or None,
                    notes=notes or None,
                )
                messages.success(
                    request,
                    "Registration submitted. An administrator will review and "
                    "activate your account.",
                )
                return redirect("ring-login")
    companies = Participant.objects.order_by("company")
    return render(request, "ring/signup.html", {"companies": companies})


@user_passes_test(user_can_manage, login_url="ring-login")
def signups(request):
    signups_qs = RingSignup.objects.select_related("django_user").order_by(
        "approved", "-created"
    )
    companies = {
        p.pk: p
        for p in Participant.objects.filter(
            pk__in={s.participant_id for s in signups_qs}
        )
    }
    for s in signups_qs:
        s._company_cache = companies.get(s.participant_id)
    pdb_signups = PeeringDBSignup.objects.select_related("django_user").order_by(
        "approved", "-created"
    )
    return render(
        request,
        "ring/signups.html",
        {"signups": signups_qs, "pdb_signups": pdb_signups},
    )


@user_passes_test(user_can_manage, login_url="ring-login")
def approve_signup(request, pk):
    signup = get_object_or_404(RingSignup, pk=pk)
    if not signup.approved:
        user = signup.django_user
        ring_user_obj = None
        ring_username = (signup.ring_username or "").strip()
        if ring_username:
            ring_user_obj = RingUser.objects.filter(username=ring_username).first()
        if ring_user_obj is None:
            ring_user_obj = ring_user(user)
        if ring_user_obj is None:
            ring_user_obj = RingUser.objects.create(
                username=ring_username or user.username,
                participant=signup.company,
                email=user.email,
                active=True,
            )
        link_ring_user(user, ring_user_obj)
        ring_user_obj.participant = signup.company
        ring_user_obj.email = user.email or ring_user_obj.email
        if request.POST.get("role") == "admin":
            ring_user_obj.admin = True
        ring_user_obj.save()
        user.is_active = True
        user.email = ring_user_obj.email or user.email
        if request.POST.get("admin_staff"):
            user.is_staff = True
        user.save()
        Token.objects.get_or_create(user=user)
        signup.approved = True
        signup.approved_by = request.user
        signup.approved_at = timezone.now()
        signup.save()
        messages.success(request, "Account %s activated." % user.username)
    return redirect("ring-signups")


@user_passes_test(user_can_manage, login_url="ring-login")
def reject_signup(request, pk):
    signup = get_object_or_404(RingSignup, pk=pk)
    if not signup.approved:
        username = signup.django_user.username
        signup.django_user.delete()
        signup.delete()
        messages.success(request, "Signup %s rejected." % username)
    return redirect("ring-signups")


# --- PeeringDB OAuth (ASN-mapped participants) ---

def _pdb_candidates(profile):
    """Networks the PDB user may act for, manageable first."""
    networks = profile.get("networks") or []
    manageable = [
        n for n in networks if n.get("perms", 0) and n.get("asn")
    ]
    if not manageable:
        return []
    creatable = [n for n in manageable if n.get("perms", 0) & 8]
    return creatable or manageable


def _pdb_username(profile):
    base = re.sub(r"[^a-z0-9._-]", ".", (profile.get("name") or "").lower())
    base = re.sub(r"\.+", ".", base).strip("._") or "pdb%s" % profile.get("id")
    candidate = base
    n = 2
    User = get_user_model()
    while (
        User.objects.filter(username=candidate).exists()
        or RingUser.objects.filter(username=candidate).exists()
    ):
        candidate = "%s-%d" % (base, n)
        n += 1
    return candidate


def _pdb_provision(request, profile, net):
    """Resolve a PDB profile+network to a user login or a pending signup.

    Returns ("user", user) once logged in, or ("pending", signup).
    """
    User = get_user_model()
    peeringdb_id = int(profile["id"])
    ring_user_obj, profile_row = ring_user_for_pdb(peeringdb_id)
    if ring_user_obj is not None and profile_row is not None:
        user = profile_row.django_user
        _pdb_sync_user(user, profile, ring_user_obj)
        auth_login(
            request, user, backend="django.contrib.auth.backends.ModelBackend"
        )
        return ("user", user)

    asn = int(net["asn"])
    participant = participant_for_pdb(asn)
    if participant is None:
        return ("pending", _pdb_create_signup(profile, net))
    set_participant_autnum(participant.pk, asn)

    user = User.objects.create_user(
        username=_pdb_username(profile),
        email=profile.get("email") or "",
        password=None,
    )
    user.is_active = True
    user.first_name = profile.get("given_name") or ""
    user.last_name = profile.get("family_name") or ""
    user.save()
    ring_user_obj = RingUser.objects.create(
        username=user.username,
        participant=participant,
        email=user.email or "",
        active=True,
    )
    link_ring_user(
        user,
        ring_user_obj,
        peeringdb_id=peeringdb_id,
        peeringdb_net_id=int(net.get("id") or 0) or None,
    )
    Token.objects.get_or_create(user=user)
    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return ("user", user)


def _pdb_sync_user(user, profile, ring_user=None):
    changed = False
    email = profile.get("email") or ""
    if email and user.email != email:
        user.email = email
        changed = True
    user.first_name = profile.get("given_name") or ""
    user.last_name = profile.get("family_name") or ""
    if ring_user is not None and ring_user.email != email:
        ring_user.email = email
        ring_user.save()
    if changed or user.first_name or user.last_name:
        user.save()


def _pdb_create_signup(profile, net):
    User = get_user_model()
    user = User.objects.create_user(
        username=_pdb_username(profile),
        email=profile.get("email") or "",
        password=None,
    )
    user.is_active = False
    user.first_name = profile.get("given_name") or ""
    user.last_name = profile.get("family_name") or ""
    user.save()
    return PeeringDBSignup.objects.create(
        django_user=user,
        peeringdb_id=int(profile["id"]),
        peeringdb_net_id=int(net.get("id") or 0),
        asn=int(net["asn"]),
        net_name=net.get("name") or ("AS%s" % net["asn"]),
    )


def peeringdb_login(request):
    if not (settings.PDB_CLIENT_ID and settings.PDB_REDIRECT_URL):
        messages.error(request, "PeeringDB login is not configured.")
        return redirect("ring-login")
    state = secrets.token_urlsafe(32)
    request.session["pdb_state"] = state
    return redirect(authorize_url(state))


def peeringdb_callback(request):
    error = request.GET.get("error")
    if error:
        return render(request, "ring/pdb_error.html", {"message": error})
    if not (settings.PDB_CLIENT_ID and settings.PDB_REDIRECT_URL):
        return render(
            request, "ring/pdb_error.html", {"message": "PeeringDB login is not configured."}
        )
    code = request.GET.get("code", "")
    state = request.GET.get("state", "")
    if state != request.session.pop("pdb_state", None):
        return render(
            request,
            "ring/pdb_error.html",
            {"message": "PeeringDB login state mismatch. Please try again."},
        )
    if not code:
        return render(
            request, "ring/pdb_error.html", {"message": "PeeringDB did not return an authorization code."}
        )
    try:
        profile = fetch_profile(exchange_code(code))
    except PDBError as exc:
        return render(request, "ring/pdb_error.html", {"message": str(exc)})
    candidates = _pdb_candidates(profile)
    if not candidates:
        return render(
            request,
            "ring/pdb_error.html",
            {"message": "Your PeeringDB account has no network permissions to act on."},
        )
    if len(candidates) > 1:
        request.session["pdb_profile"] = profile
        return redirect("ring-pdb-pick")
    return _pdb_enter(request, profile, candidates[0])


def peeringdb_pick(request):
    profile = request.session.get("pdb_profile")
    if not profile:
        return redirect("ring-pdb-login")
    candidates = _pdb_candidates(profile)
    if request.method == "POST":
        try:
            asn = int(request.POST.get("asn", ""))
        except ValueError:
            asn = None
        net = next((n for n in candidates if int(n.get("asn")) == asn), None)
        request.session.pop("pdb_profile", None)
        if net is None:
            return render(
                request, "ring/pdb_error.html", {"message": "That network is not available for your account."}
            )
        return _pdb_enter(request, profile, net)
    return render(
        request,
        "ring/pdb_pick.html",
        {"candidates": sorted(candidates, key=lambda n: n.get("asn"))},
    )


def _pdb_enter(request, profile, net):
    kind, obj = _pdb_provision(request, profile, net)
    if kind == "pending":
        return render(request, "ring/pdb_pending.html", {})
    messages.success(request, "Logged in via PeeringDB.")
    return redirect("/")


@user_passes_test(user_can_manage, login_url="ring-login")
def approve_peeringdb_signup(request, pk):
    signup = get_object_or_404(PeeringDBSignup, pk=pk)
    if not signup.approved:
        user = signup.django_user
        participant = participant_for_pdb(signup.asn, signup.net_name)
        if participant is None:
            try:
                participant = Participant.objects.create(
                    company=signup.net_name or "AS%s" % signup.asn,
                    contact=signup.net_name or "",
                    email=user.email or "",
                )
            except IntegrityError:
                participant = Participant.objects.filter(
                    company__iexact=(signup.net_name or "").strip()
                ).first()
                if participant is None:
                    raise
        set_participant_autnum(participant.pk, signup.asn)
        ring_user_obj = RingUser.objects.create(
            username=user.username,
            participant=participant,
            email=user.email or "",
            active=True,
        )
        link_ring_user(
            user,
            ring_user_obj,
            peeringdb_id=signup.peeringdb_id,
            peeringdb_net_id=signup.peeringdb_net_id,
        )
        user.is_active = True
        user.email = ring_user_obj.email or user.email
        user.save()
        Token.objects.get_or_create(user=user)
        signup.approved = True
        signup.approved_by = request.user
        signup.approved_at = timezone.now()
        signup.save()
        messages.success(request, "Account %s activated for AS%s." % (user.username, signup.asn))
    return redirect("ring-signups")


@user_passes_test(user_can_manage, login_url="ring-login")
def reject_peeringdb_signup(request, pk):
    signup = get_object_or_404(PeeringDBSignup, pk=pk)
    if not signup.approved:
        username = signup.django_user.username
        signup.django_user.delete()
        signup.delete()
        messages.success(request, "PeeringDB signup %s rejected." % username)
    return redirect("ring-signups")