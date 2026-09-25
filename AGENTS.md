# AGENTS.md

## Repo state

Small local git history (branch `devel` tracks `origin/devel`, in sync with
`main`), working tree clean. This is a Django 5.2+ port
("ring-portal") of the NLNOG Ring admin tool. Verified environment: `venv/`
(Django 5.2.x + deps, Python 3.14), SQLite, DEBUG=True. Migrations exist and
are applied against `db.sqlite3` (gitignored).
`ring.sql` (gitignored, ~986MB mysqldump) is the dev seed source used by the
`ring_seed` command. No CI, no formatter/linter manifest yet. The DB backend is
selected by env: default SQLite, `DB_ENGINE=mysql` (PyMySQL drop-in) with
`DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT`. The app is dual-DB capable: the
9 legacy-backed models read from a read-only MySQL alias (`legacy`) when
`DB_ENGINE=mysql` or `RING_LEGACY_DB=1`, otherwise they use SQLite mirror
tables (dev/tests). All app-added data (auth, tokens, signups, profiles,
network links) lives exclusively in SQLite. Install deps with `venv/bin/python -m pip`.

## Layout

- `ring/` — the Django app:
  - `models.py` — 9 legacy-backed models mapped to the original ring DB tables
    via `db_table` (bare names: `participants`, `users`, `machines`, `sshkeys`,
    `sshhostkeys`, `premarks`, `mremarks`, `ansible`, `health`): `Participant`,
    `RingUser`, `Machine`, `SSHKey`, `SSHHostKey`, `ParticipantRemark`,
    `MachineRemark`, `AnsibleRun`, `HealthReport`. Plus SQLite-only models:
    `RingSignup` (self-service access requests; `participant_id` is a plain
    IntegerField — no cross-DB FK — with a cached `company` property resolving
    the `Participant`), `PeeringDBSignup` (pending PeeringDB-driven
    registrations waiting on admin approval), `RingUserProfile`
    (`django_user` 1:1 with `auth.User`, `ring_user_id`, `peeringdb_id`,
    `peeringdb_net_id` primary SSO binding), `PeeringDBNetwork` (one row per
    user/network: `django_user`, `peeringdb_net_id`, `asn`, `net_name`,
    `participant_id` IntegerField — the multi-network SSO link set that powers
    the org switcher), `ParticipantProfile` (`participant_id`,
    `autnum` PeeringDB ASN identity). Never add FK/OneToOne columns pointing
    from legacy tables to SQLite tables: cross-database relations are
    forbidden; put support data in these profile tables instead.
    `tstamp`/`last_active` are `EpochDateTimeField(null=True)` (INTEGER epoch
    in DB, aware datetime in Python, `0`/NULL reads as `None`);
    booleans are `BooleanField(null=True)` (legacy NULL == off).
    `HealthReport.summary` is a JSONField. `Machine.short_hostname` property +
    `zone.py` (`fqdn`/`short`) handle FQDN/short-zone conversion
    (`machines.hostname` is FQDN; `ansible`/`health` hostnames are short).
    `AnsibleRun`/`HealthReport` carry a composite `Index(["hostname",
    "-timestamp"])` for the 7-day dashboards.
  - `fields.py` — `EpochDateTimeField` (INTEGER epoch persistence with
    DateTimeField semantics; `db_type` INTEGER, `get_internal_type`
    BigIntegerField to dodge backend datetime converters).
  - `dbrouters.py` — `RingRouter`: routes Group A reads/writes to the `legacy`
    alias when configured (`"legacy" in settings.DATABASES`), else degrades to
    `default` (SQLite mirror mode). `allow_migrate` refuses all DDL on
    `legacy`; `allow_relation` disallows cross-DB relations.
  - `services/profiles.py` — the app-facing layer for linked-user/ASN identity:
    `ring_user(user)`, `profile_for_ring_user`, `ring_user_for_pdb`,
    `link_ring_user`, `link_pdb_network`, `pdb_networks(user)`,
    `member_participant_ids(user)` (legacy org + all linked networks),
    `active_participant_id(request)`/`active_participant(request)`
    (session-selected org, falling back to the legacy org then first link),
    `switch_active_participant(request, pk)`, `participant_autnum`,
    `participant_for_autnum`, `set_participant_autnum`, plus
    `legacy_writable()` / `assert_legacy_writable()` (raise
    `LegacyReadonlyError`; gated by `RING_LEGACY_WRITE_ENABLED=1`). Use these
    from views/commands — never reach into profile tables directly.
  - `kpi_cache.py` — LocMemCache-backed KPI aggregates (`cached_failed_7d`,
    `cached_ubuntu_releases`) keyed by a version (bumped by
    `ring_process_status`) + hourly bucket, TTL 10min. Dashboards call these
    instead of running the raw aggregates; never re-add raw query in views.
  - `api/` — DRF: `serializers.py` (write-side FK containment per caller org),
    `permissions.py` (org-scoped write authz + admin bypass), `viewsets.py`
    (ReadWrite viewsets; `ansible`/`health` are POST-only ingest restricted to
    the caller's own participant machines), `views.py`
    (`account/login` token + `account/me`); `/'` mounts: participants, users,
    machines, sshkeys, sshhostkeys, premarks, mremarks, ansible, health,
    account/*; `api-auth/` for browsable API.
  - Web auth: `views.py` `signup`/`signups`/`approve_signup`/`reject_signup`,
    plus PeeringDB OAuth: `peeringdb_login`/`peeringdb_callback`/
    `peeringdb_pick` (multi-network selection — a PDB user with several
    networks picks a *set* via checkboxes; each is matched to a participant,
    linked via `PeeringDBNetwork`, and unmatched ones are reported) and
    `approve_peeringdb_signup`/`reject_peeringdb_signup`, plus
    `participant_switch` (`/participants/<pk>/switch/`, session org
    switcher), `context_processors.py` (`user_can_manage`, `pdb_enabled`,
    `active_participant`, `user_organisations`), URLs under
    `/accounts/{login,logout,signup,signups,peeringdb/*}`. Models, migrations,
    templates and tests all cover these.
  - Role split: `user_can_manage` (ring admins) get the full dashboards:
    `index` (`/`), `machines`, `participants`, `users`, `participant_info` —
    these are `@admin_or_portal` (regular members get redirected to `/my/`).
    Regular linked members land on `my_portal` (`/my/`, `ring-my`: active
    company, account, PeeringDB binding and their nodes + issues) and may
    view `machine_detail`/`machine_status` and edit *any* participant they are
    linked to, scoped by the session "active org" (multi-org members switch
    via the header switcher; the API still scopes to the legacy RingUser org).
    Never leak other organisations' names/emails to members; the issue
    scanner helper `_scoped_issues(queryset, since)` + `_issue_summary()`
    keep member issue views scoped to their own nodes.
  - `services/` — `geocoding.py` (pycountry/nominatim, lru_cache), `nodestatus.py`
    (port of `ansible_process`), `ansiblefiles.py` (hostfile/hostkeyfile/userfile
    generators), `deploy.py` (git/rsync for ring-ansible + ring-web),
    `mail.py` (all template strings + generate_* functions), `webpost.py`
    (Hugo post generation/publish), `peeringdb.py` (OAuth authorize/token/profile
    via `PDB_*` settings, stdlib urllib). All paths come from `RING_*` settings.
  - `management/commands/` — `ring_seed` (loads `ring.sql`; mirror-mode only,
    guarded by `assert_legacy_writable()`), `ring_process_status`,
    `ring_ansible_deploy`, `ring_scan_hostkeys`, `ring_generate_webpost`,
    `ring_generate_mail`, `ring_dnscommands`, `ring_purge_machines`,
    `ring_backfill_asn` (writes `ParticipantProfile.autnum` from machines,
    never the legacy DB), `ring_dbreadonly_check` (proves the configured
    `legacy` DB user is SELECT-only: SHOW GRANTS + write probes).
  - `templates/ring/` — `base.html` + `index.html` (KPIs, Chart.js), `machines.html`
    (list+filters), `machine_detail.html`, `participants.html` (info modal),
    `participants_edit.html`, `users.html`, `login.html`, `signup.html`,
    `signups.html`, `pdb_error.html`/`pdb_pick.html`/`pdb_pending.html`. Templates
    use the `ago`/`days_ago` filters from `templatetags/ring_filters.py`.
  - `tests/` — `test_api.py` (CRUD, ingest auth, org-scoped authz, signup flow,
    account endpoints, participant self-edit, paginated list, PeeringDB OAuth
    provisioning/approval with mocked OAuth endpoints incl. multi-network
    selection + org-switch scoping, asn backfill),
    `test_nodestatus.py` (activate/deactivate/alive/guard transitions),
    `test_kpi_cache.py` (cached aggregates, version-bump invalidation, 0-query
    re-calls), `test_profiles.py` (profile helpers + EpochDateTimeField
    round-trip). Do NOT re-add `ring/tests.py`; it would shadow the package.
    Web-auth tests must use the Django `Client` with a *session login* — DRF
    `force_authenticate` does not cover plain Django views.
- `ringweb/` — project config: `INSTALLED_APPS` already lists `ring` +
  `rest_framework` + `rest_framework.authtoken`. URLs: `admin/`, `api/`,
  `""` (dashboards). `RING_*` settings block: zone, ansible dirs, web dirs,
  geocode URL, mailhost, adminemail, ringusersemail. `PDB_*` settings block:
  OAuth endpoint, client id/secret, redirect URL (env-selected; the PeeringDB
  login button only renders when id+redirect are set). Tracked settings live in
  `settings.py.example` (no real secrets — everything sensitive comes from env
  at boot); the working copy is `settings.py`, which is gitignored.
- `ring-admin.py` — the legacy Python 2-era standalone CLI this port replaces.
  Treat it as reference material, **not runnable code** (imports MySQLdb,
  uses Py2-only `raw_input`, hardcodes MySQL + `/var/ring/` paths). Do not run it.
- `schema.sql` — placeholder; the real SCHEMA is embedded in `ring-admin.py`.

## Commands

Always invoke Python via the venv; run from repo root:

- Run server: `venv/bin/python manage.py runserver`
- Migrations: `venv/bin/python manage.py makemigrations` then `migrate`
- Tests: `venv/bin/python manage.py test ring`
- Single test: `venv/bin/python manage.py test ring.tests.test_api.ApiCrudTest.test_machine_search`
- System/URL check: `venv/bin/python manage.py check`
- Seed from dump: `venv/bin/python manage.py ring_seed` (full; re-seed status
  tables only: `ring_seed --only ansible health`). Takes ~11 min per table
  group, writes in batches of 5000. Flags: `--only`, `--full`, `--since`,
  `--no-clear`.
- Process status: `venv/bin/python manage.py ring_process_status [--send]`
- Backfill participant ASNs: `venv/bin/python manage.py ring_backfill_asn`
  (populates `ParticipantProfile.autnum` from machines; reports participants
  whose machines span multiple ASNs for manual review).
- Legacy read-only verification: `venv/bin/python manage.py
  ring_dbreadonly_check` (expects a SELECT-only `legacy` MySQL account; write
  probes must be rejected).

## Gotchas

- Run `manage.py` from the repo root (SQLite db lives at `BASE_DIR/db.sqlite3`).
- DB backend is env-selected at import time in `ringweb/settings.py` (the
  committed template is `ringweb/settings.py.example`, referenced from below as
  `ringweb/settings.py`; your local `settings.py` is gitignored — copy the
  example to a working copy); there is no runtime switching. MySQL mode requires
  a running server and a database whose schema is migrated (or the legacy ring
  DB, see README).
- In live mode (`RING_LEGACY_DB=1` / `DB_ENGINE=mysql`) the 9 legacy models
  read/write the `legacy` MySQL alias and are SELECT-only in practice:
  `allow_migrate` blocks DDL, and every write path is guarded by
  `assert_legacy_writable()`/`legacy_writable()` (management commands,
  DRF viewsets, web-auth views — participant edit, signup/PDB approval,
  PDB provisioning — and the Django admin via `LegacyReadonlyAdminMixin`).
  Group A migrations still run against `default` so the SQLite mirror
  (dev/tests) matches. Set `RING_LEGACY_WRITE_ENABLED=1` to re-enable writes
  (dev only).
- The DB user for `legacy` must be SELECT-only; verify with
  `ring_dbreadonly_check` before pointing the live app at it.
- Legacy domain semantics (user override of `machines.owner`, NULL==off flags,
  short-vs-FQDN hostnames, `>10` deactivation/dead guards) come from
  `ring-admin.py`'s `cmd_*` / `ansible_process`; port faithfully, do not invent.
- `Machine.short_hostname` and health/ansible `hostname` fields are the short
  name; `machines.hostname` is the FQDN. Join the two explicitly.
- Seed data lives in `db.sqlite3` only; never commit it, and never commit
  `ring.sql`.
- Write authz (`ring/api/permissions.py`): reads are public; ring admins
  (Django staff/superuser or a linked `RingUser` with legacy `admin` flag) may
  write anything; ordinary linked users may only create/update/delete objects
  of their own participant (FK containment in the serializers) and ingest
  ansible/health for their own active machines. Only admins can grant
  `RingUser.admin` or create participants.
- Management commands that touch `/var/ring` (`ansible_deploy`, `webpost
  --publish`, `dnscommands`) only work where `RING_*` paths exist; dashboards
  and the API never touch the filesystem.