import ast
import datetime
import json
from collections import OrderedDict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

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
from ring.services.profiles import assert_legacy_writable

# Column order as dumped by mysqldump (matches the live DB schema, which has
# drifted from the SCHEMA string embedded in ring-admin.py).
COLUMNS = {
    "ansible": ["id", "timestamp", "hostname", "unreachable", "ok", "changed", "skipped", "failures"],
    "health": ["id", "timestamp", "hostname", "family", "summary"],
    "machines": ["id", "hostname", "v4", "v6", "autnum", "country", "state", "city", "dc", "geo", "owner", "tstamp", "active", "last_active", "alive_v4", "alive_v6"],
    "mremarks": ["id", "remark", "tstamp", "machine"],
    "participants": ["id", "company", "url", "contact", "email", "nocemail", "companydesc", "public", "tstamp"],
    "premarks": ["id", "remark", "tstamp", "participant"],
    "sshhostkeys": ["id", "keytype", "sshkey", "keyid", "machine"],
    "sshkeys": ["id", "keytype", "sshkey", "keyid", "user"],
    "users": ["id", "username", "userid", "active", "participant", "admin", "shell", "email"],
}

MODELS = {
    "ansible": AnsibleRun,
    "health": HealthReport,
    "machines": Machine,
    "mremarks": MachineRemark,
    "participants": Participant,
    "premarks": ParticipantRemark,
    "sshhostkeys": SSHHostKey,
    "sshkeys": SSHKey,
    "users": RingUser,
}

# Insert order respects FK dependencies independent of dump ordering.
TABLE_ORDER = [
    "participants",
    "users",
    "machines",
    "sshkeys",
    "sshhostkeys",
    "premarks",
    "mremarks",
    "ansible",
    "health",
]

EPOCH_FIELDS = {"tstamp", "last_active"}
BOOL_FIELDS = {"active", "alive_v4", "alive_v6", "admin", "public"}
TIME_FIELDS = {"timestamp"}
JSON_FIELDS = {"summary"}
FK_FIELDS = {"participant", "user", "owner", "machine"}


def _to_bool(value):
    if value is None:
        return None
    return bool(value)


def _to_time(value):
    if value is None or value == 0:
        return None
    if isinstance(value, int):
        return datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)
    naive = datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return timezone.make_aware(naive, datetime.timezone.utc)


def mysql2py(raw):
    """Convert a MySQL VALUES tuple like (1,'x',NULL,'{...}') to a Python tuple.

    Handles backslash escapes and single quotes without trusting arbitrary
    global substitutions (NULL outside quotes -> None).
    """
    parts = []
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == "'":
            j = i + 1
            while j < n:
                if raw[j] == "\\":
                    j += 2
                    continue
                if raw[j] == "'":
                    break
                j += 1
            s = raw[i : j + 1]
            parts.append(s.replace("\\0", "\\x00"))
            i = j + 1
        elif raw.startswith("NULL", i):
            parts.append("None")
            i += 4
        else:
            parts.append(c)
            i += 1
    try:
        return ast.literal_eval("".join(parts))
    except (ValueError, SyntaxError) as exc:
        raise CommandError("could not parse dump row: %s... (%s)" % (raw[:120], exc))


def iter_rows(text):
    """Yield each parenthesized tuple in a single extended-INSERT VALUES body."""
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "(":
            i += 1
            continue
        j = i
        depth = 0
        while j < n:
            c = text[j]
            if c == "'":
                j += 1
                while j < n:
                    if text[j] == "\\":
                        j += 2
                        continue
                    if text[j] == "'":
                        break
                    j += 1
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        yield mysql2py(text[i : j + 1])
        i = j + 1


def convert_row(table, row):
    cols = COLUMNS[table]
    values = dict(zip(cols, row))
    out = {"id": values["id"]}
    for name, value in values.items():
        if name == "id":
            continue
        if name in BOOL_FIELDS:
            out[name] = _to_bool(value)
        elif name in EPOCH_FIELDS:
            out[name] = _to_time(value)
        elif name in TIME_FIELDS:
            out[name] = _to_time(value)
        elif name in JSON_FIELDS:
            out[name] = json.loads(value) if value else None
        elif name in FK_FIELDS:
            out[name + "_id"] = value
        elif name in ("v4", "v6"):
            out[name] = value or None
        else:
            if isinstance(value, str):
                value = value.strip()
            out[name] = value
    return out


class Command(BaseCommand):
    help = "Import a mysqldump of the legacy `ring` database into Django models."

    def add_arguments(self, parser):
        parser.add_argument("--path", default=str(settings.BASE_DIR / "ring.sql"))
        parser.add_argument("--since", help="Only import ansible/health rows newer than this (default: 90 days). Format: YYYY-MM-DD or days int.")
        parser.add_argument("--no-clear", action="store_true", help="Do not delete existing ring data before importing.")
        parser.add_argument("--full", action="store_true", help="Import all ansible/health rows instead of the rolling window.")
        parser.add_argument(
            "--only",
            nargs="+",
            help="Only import these tables (e.g. --only ansible health).",
        )

    def handle(self, *args, **options):
        assert_legacy_writable()
        path = options["path"]
        try:
            self.fh = open(path, encoding="utf-8", errors="replace")
        except OSError as exc:
            raise CommandError("cannot open dump: %s" % exc)

        if options["full"]:
            since = None
        else:
            since = self._parse_since(options["since"])
        if since:
            self.stdout.write("ansible/health window: since %s" % since)

        offsets = self._scan_offsets()
        if not options["no_clear"]:
            self._clear(options["only"])

        for table in TABLE_ORDER:
            if options["only"] and table not in options["only"]:
                continue
            total, skipped = 0, 0
            if table not in offsets:
                continue
            batch = []
            for off, size in offsets[table]:
                self.fh.seek(off)
                body = self.fh.read(size)
                start = body.index("VALUES") + len("VALUES")
                for tup in iter_rows(body[start:]):
                    if table in ("ansible", "health") and since:
                        ts = _to_time(tup[1])
                        if ts is not None and ts < since:
                            skipped += 1
                            continue
                    row = convert_row(table, tup)
                    batch.append(MODELS[table](**row))
                    if len(batch) >= 5000:
                        MODELS[table].objects.bulk_create(batch, ignore_conflicts=False)
                        total += len(batch)
                        batch = []
            if batch:
                MODELS[table].objects.bulk_create(batch)
                total += len(batch)
            self.stdout.write(
                "%-13s imported %d rows (skipped %d)" % (table, total, skipped)
            )
        self.stdout.write(self.style.SUCCESS("seed complete"))

    def _parse_since(self, value):
        if not value:
            cutoff = timezone.now() - datetime.timedelta(days=90)
            return cutoff
        if value.isdigit():
            return timezone.now() - datetime.timedelta(days=int(value))
        return datetime.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)

    def _scan_offsets(self):
        offsets = OrderedDict()
        self.fh.seek(0)
        while True:
            off = self.fh.tell()
            line = self.fh.readline()
            if not line:
                break
            line = line.strip()
            if line.startswith("INSERT INTO `"):
                table = line.split("`", 2)[1]
                offsets.setdefault(table, []).append((off, len(line)))
        return offsets

    def _clear(self, only=None):
        for table in reversed(TABLE_ORDER):
            if only and table not in only:
                continue
            MODELS[table].objects.all().delete()