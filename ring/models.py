from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from ring.fields import EpochDateTimeField


class Participant(models.Model):
    company = models.CharField(max_length=255, unique=True)
    url = models.CharField(max_length=255, blank=True, null=True)
    contact = models.CharField(max_length=255, default="", blank=True)
    email = models.EmailField(max_length=255, default="", blank=True)
    nocemail = models.EmailField(max_length=255, default="", blank=True)
    companydesc = models.TextField(blank=True, null=True)
    public = models.BooleanField(default=None, null=True)
    tstamp = EpochDateTimeField(blank=True, null=True)

    class Meta:
        db_table = "participants"
        ordering = ["company"]

    def __str__(self):
        return self.company


class RingUser(models.Model):
    username = models.CharField(max_length=255, unique=True)
    userid = models.IntegerField(blank=True, null=True)
    shell = models.CharField(max_length=255, blank=True, null=True)
    active = models.BooleanField(default=None, null=True)
    participant = models.ForeignKey(
        Participant,
        on_delete=models.RESTRICT,
        related_name="users",
        db_column="participant",
    )
    admin = models.BooleanField(default=None, null=True)
    email = models.EmailField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "users"
        ordering = ["username"]

    def __str__(self):
        return self.username

    @property
    def is_ring_admin(self):
        return bool(self.admin)


class Machine(models.Model):
    hostname = models.CharField(max_length=255, unique=True)
    v4 = models.GenericIPAddressField(protocol="IPv4", unique=True, blank=True, null=True)
    v6 = models.GenericIPAddressField(protocol="IPv6", unique=True, blank=True, null=True)
    autnum = models.IntegerField()
    country = models.CharField(max_length=2, blank=True, null=True)
    state = models.CharField(max_length=2, blank=True, null=True)
    city = models.CharField(max_length=255, blank=True, null=True)
    dc = models.CharField(max_length=2048, blank=True, null=True)
    geo = models.CharField(max_length=255, blank=True, null=True)
    owner = models.ForeignKey(
        RingUser,
        on_delete=models.RESTRICT,
        related_name="machines",
        db_column="owner",
    )
    tstamp = EpochDateTimeField(blank=True, null=True)
    active = models.BooleanField(default=None, null=True)
    alive_v4 = models.BooleanField(default=None, null=True)
    alive_v6 = models.BooleanField(default=None, null=True)
    last_active = EpochDateTimeField(blank=True, null=True)

    class Meta:
        db_table = "machines"
        ordering = ["hostname"]

    def __str__(self):
        return self.hostname

    @property
    def short_hostname(self):
        from ring.zone import short

        return short(self.hostname)


class SSHKey(models.Model):
    keytype = models.CharField(max_length=255)
    sshkey = models.CharField(max_length=16384, blank=True, null=True)
    keyid = models.CharField(max_length=255, blank=True, null=True)
    user = models.ForeignKey(
        RingUser,
        on_delete=models.RESTRICT,
        related_name="sshkeys",
        db_column="user",
    )

    class Meta:
        db_table = "sshkeys"
        ordering = ["id"]

    def __str__(self):
        return "%s key %s" % (self.keytype, self.user_id or self.id)


class SSHHostKey(models.Model):
    keytype = models.CharField(max_length=255)
    sshkey = models.CharField(max_length=16384)
    keyid = models.CharField(max_length=255, blank=True, null=True)
    machine = models.ForeignKey(
        Machine,
        on_delete=models.RESTRICT,
        related_name="hostkeys",
        db_column="machine",
    )

    class Meta:
        db_table = "sshhostkeys"
        ordering = ["id"]

    def __str__(self):
        return "%s key %s" % (self.keytype, self.machine_id or self.id)


class ParticipantRemark(models.Model):
    remark = models.TextField(blank=True, null=True)
    tstamp = EpochDateTimeField(blank=True, null=True)
    participant = models.ForeignKey(
        Participant,
        on_delete=models.RESTRICT,
        related_name="remarks",
        db_column="participant",
    )

    class Meta:
        db_table = "premarks"
        ordering = ["-tstamp"]

    def __str__(self):
        return "remark on %s" % self.participant_id


class MachineRemark(models.Model):
    remark = models.TextField(blank=True, null=True)
    tstamp = EpochDateTimeField(blank=True, null=True)
    machine = models.ForeignKey(
        Machine,
        on_delete=models.RESTRICT,
        related_name="remarks",
        db_column="machine",
    )

    class Meta:
        db_table = "mremarks"
        ordering = ["-tstamp"]

    def __str__(self):
        return "remark on %s" % self.machine_id


class AnsibleRun(models.Model):
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    hostname = models.CharField(max_length=128, db_index=True, blank=True, null=True)
    unreachable = models.PositiveIntegerField(blank=True, null=True)
    ok = models.PositiveIntegerField(blank=True, null=True)
    changed = models.PositiveIntegerField(blank=True, null=True)
    skipped = models.PositiveIntegerField(blank=True, null=True)
    failures = models.PositiveIntegerField(blank=True, null=True)

    class Meta:
        db_table = "ansible"
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["hostname", "-timestamp"])]

    def __str__(self):
        return "%s %s" % (self.hostname, self.timestamp)


class HealthReport(models.Model):
    FAMILY_CHOICES = [(4, "IPv4"), (6, "IPv6")]

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    hostname = models.CharField(max_length=255, db_index=True, blank=True, null=True)
    family = models.IntegerField(choices=FAMILY_CHOICES, blank=True, null=True)
    summary = models.JSONField(blank=True, null=True)

    class Meta:
        db_table = "health"
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["hostname", "-timestamp"])]

    def __str__(self):
        return "%s/%s %s" % (self.hostname, self.family, self.timestamp)

    @property
    def info(self):
        if not self.summary:
            return {}
        return self.summary.get("info") or {}

    @property
    def health(self):
        if not self.summary:
            return {}
        return self.summary.get("health") or {}

    @property
    def failure_checks(self):
        return sorted(k for k, v in self.health.items() if v is False)

    @property
    def needs_reboot(self):
        return bool(self.info.get("needs_reboot"))

    @property
    def ubuntu_updates(self):
        return self.info.get("ubuntu_updates") or 0

    @property
    def ubuntu_security_updates(self):
        return self.info.get("ubuntu_security_updates") or 0


class RingSignup(models.Model):
    """Pending self-service registration, awaiting admin approval."""

    django_user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="ring_signup",
    )
    participant_id = models.IntegerField()
    ring_username = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created = models.DateTimeField(auto_now_add=True)
    approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        get_user_model(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_signups",
    )
    approved_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["created"]

    def __str__(self):
        return "signup %s for %s" % (self.django_user_id, self.participant_id)

    @property
    def company(self):
        """Resolve the legacy Participant for this signup (cross-database)."""
        cached = getattr(self, "_company_cache", None)
        if cached is not None:
            return cached
        company = Participant.objects.filter(pk=self.participant_id).first()
        self._company_cache = company
        return company


class PeeringDBSignup(models.Model):
    """Pending PeeringDB-driven registration for an ASN we do not know yet."""

    django_user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="peeringdb_signup",
    )
    peeringdb_id = models.IntegerField(unique=True)
    peeringdb_net_id = models.IntegerField()
    asn = models.IntegerField()
    net_name = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created = models.DateTimeField(auto_now_add=True)
    approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        get_user_model(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_peeringdb_signups",
    )
    approved_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["created"]

    def __str__(self):
        return "peeringdb signup AS%s for %s" % (self.asn, self.django_user_id)


class RingUserProfile(models.Model):
    """App-side (SQLite) extensions for a legacy ``users`` row.

    Keyed by the legacy integer user id because the legacy table is read-only
    and Django forbids foreign keys across databases.
    """

    django_user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="ring_profile",
    )
    ring_user_id = models.IntegerField(unique=True)
    peeringdb_id = models.IntegerField(null=True, blank=True, unique=True)
    peeringdb_net_id = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return "profile for user %s" % self.ring_user_id


class PeeringDBNetwork(models.Model):
    """App-side (SQLite) link between a Django user and a PeeringDB network.

    A person may act for several networks (each mapped to one RING
    participant). ``participant_id`` is a plain integer column because the
    participant lives in the read-only legacy database and Django forbids
    foreign keys across databases.
    """

    django_user = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="pdb_networks",
    )
    peeringdb_net_id = models.IntegerField()
    asn = models.IntegerField()
    net_name = models.CharField(max_length=255, blank=True, null=True)
    participant_id = models.IntegerField()

    class Meta:
        unique_together = [("django_user", "peeringdb_net_id")]
        ordering = ["id"]

    def __str__(self):
        return "PDB net AS%s for user %s" % (self.asn, self.django_user_id)


class ParticipantProfile(models.Model):
    """App-side (SQLite) extensions for a legacy ``participants`` row."""

    participant_id = models.IntegerField(unique=True)
    autnum = models.IntegerField(null=True, blank=True, unique=True)

    def __str__(self):
        return "profile for participant %s" % self.participant_id