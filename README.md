# ring-portal

A Django 5.2 port of the legacy NLNOG RING admin tool (`ring-admin.py`).

The legacy tool was a Python 2-era standalone CLI (MySQL + `/var/ring/` paths).
This port keeps all of its domain semantics but modernizes the stack: a Django
app with models, a DRF management API, node-status processing, deployment and
notification plumbing as management commands, and browser dashboards.

## Requirements

- Python 3.10+
- Django 5.2.x (`>=5.2,<6`)
- `djangorestframework`, `pycountry`, `Unidecode`
- SQLite by default; `PyMySQL` for the MySQL/MariaDB backend

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

## Database backends

The backend is selected at boot by environment variables (`ringweb/settings.py`).
Default is SQLite (dev seed in `db.sqlite3`). To use MySQL/MariaDB instead:

```sh
DB_ENGINE=mysql \
DB_NAME=ring DB_USER=ring DB_PASSWORD=secret \
DB_HOST=127.0.0.1 DB_PORT=3306 \
.venv/bin/python3.12 manage.py migrate
```

`DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT` default to `ring`/`ring`/`""`/
`127.0.0.1`/`3306`. `PyMySQL` is installed as the MySQLdb drop-in. Run
`manage.py migrate` against a fresh database, or point it at the existing
legacy `ring` database (schema is managed via Django migrations).

## Seeding a development database

The dev seed source is `ring.sql` (a ~986&nbsp;MB mysqldump of the legacy
`ring` database — gitignored). Place it at the repo root, then:

```sh
# full import (all tables, ~23 min: ansible + health groups take ~11 min each)
.venv/bin/python manage.py ring_seed

# status tables only (fast re-seed after model/seed changes)
.venv/bin/python manage.py ring_seed --only ansible health
```

Flags:

| Flag | Meaning |
|---|---|
| `--path PATH` | Path to the mysqldump (default: `ring.sql` in repo root) |
| `--only TABLES` | Import only these tables (e.g. `--only ansible health`) |
| `--since DATE\|DAYS` | Only ansible/health rows newer than this (default: 90 days) |
| `--full` | Import **all** ansible/health rows (no cutoff) |
| `--no-clear` | Do not delete existing ring data before importing |

Seed data lives in `db.sqlite3` only; never commit the database or `ring.sql`.

## Running the server

```sh
.venv/bin/python manage.py runserver
```

- `/` — overview dashboard (KPIs, Ubuntu release distribution, failed runs)
- `/machines/` — machine list with search/country/state/active/Ubuntu filters
- `/machines/<hostname>/` — machine detail (status, map, host keys, remarks, recent health/ansible)
- `/participants/` — participant list (info modals, edit own organisation)
- `/users/` — ring user/account list
- `/accounts/signup/` — self-service access request
- `/accounts/signups/` — approve/reject pending requests (ring admins)
- `/api/` — DRF browsable API
- `/api/account/login/`, `/api/account/me/` — token login + current profile
- `/api/api-auth/` — login for the browsable API
- `/admin/` — Django admin (all models registered, incl. signup requests)

## REST API

Read endpoints are public. Writes require an authenticated Django user —
ordinary users may only touch objects of their **own participant**, while
ring administrators (Django staff/superuser or a linked `RingUser` with the
legacy `admin` flag) can write anything. Role and scope rules:

- Participant: reads public; create/delete admin-only; update own participant.
- RingUser/Machine/SSHKey/PMark/MMark: full CRUD scoped to the caller's own
  participant (FK containment is enforced: e.g. `owner`/`machine`/`user` must
  resolve inside your participant, and only an admin can set `admin: true`).
- Ansible/health ingest: admins may push for any host; ordinary users only for
  their own participant's active machines.
- Deleting a record that others still reference returns a 400 instead of a 500.

Token auth is via `rest_framework.authtoken`. Get a token with
`POST /api/account/login/` (`username` + `password` → `{token, ...profile}`)
or from `GET /api/account/me/` (authenticated) — a token is issued on signup
approval.

| Resource | Endpoint | Notes |
|---|---|---|
| Participants | `/api/participants/` | CRUD + `?search=` / `?ordering=` |
| Users | `/api/users/` | CRUD, includes machine hostnames |
| Machines | `/api/machines/` | CRUD, includes `short_hostname` |
| SSH keys | `/api/sshkeys/` | CRUD |
| SSH host keys | `/api/sshhostkeys/` | CRUD |
| Participant remarks | `/api/premarks/` | CRUD |
| Machine remarks | `/api/mremarks/` | CRUD |
| Ansible runs | `/api/ansible/` | **POST-only** ingest |
| Health reports | `/api/health/` | **POST-only** ingest |

The `ansible` and `health` endpoints accept a single object or a list
(batch ingest). Example:

```sh
curl -H "Authorization: Token $TOKEN" -X POST https://.../api/health/ \
  -H "Content-Type: application/json" \
  -d '{"hostname":"node01","family":4,"summary":{"info":{"success":true},"health":{"disk":true}}}'
```

Hostnames in ansible/health records are the **short** node names; machine
records use the FQDN (`machine.short_hostname` does the conversion).

## Management commands

All commands run from the repo root via `.venv/bin/python manage.py ...`.

| Command | Purpose |
|---|---|
| `ring_seed` | Import the mysqldump (see above) |
| `ring_process_status [--send]` | Reconcile node status from the last 24h of ansible/health data: auto-activates/deactivates machines, updates `alive_v4`/`alive_v6`, reports failed runs and Ubuntu release distribution. `--send` emails the report to ring-admins |
| `ring_ansible_deploy` | Deploy the ring-ansible repo (checkout, fetch ssh keys via rsync, regenerate hostfile/hostkeyfile/userfile, commit, push) |
| `ring_scan_hostkeys [hostname]` | `ssh-keyscan` one or all active machines and reconcile `sshhostkeys` |
| `ring_generate_webpost <username> [--publish]` | Generate a Hugo "joined the RING" post; `--publish` commits/pushes it to ring-web |
| `ring_generate_mail <kind> <arg> [--send]` | Generate (and optionally send) notification mail: `welcome`, `announce` (take `<username>`); `down`, `remove`, `failedupgrade`, `cannotupgrade`, `disk` (take `<node>`); `downreminders` (lists/sends all down reminders) |
| `ring_dnscommands <hostname>` | Print the `ring-pdns` add/activate commands for a node |
| `ring_purge_machines [--days N]` | Delete inactive machines past the window (default 90 days), their host keys/remarks, and deactivate participants left without machines |

Filesystem-touching commands (`ring_ansible_deploy`, `ring_generate_webpost
--publish`, `ring_dnscommands`) require `RING_*` paths to exist (see settings);
dashboards and the API never touch the filesystem.

## Configuration

Domain constants live in a `RING_*` block in `ringweb/settings.py`:

| Setting | Default | Used for |
|---|---|---|
| `RING_ZONE` | `ring.nlnog.net` | FQDN/short-name conversion |
| `RING_ADMINEMAIL` / `RING_RINGUSERSEMAIL` | `ring-admins@…` / `ring-users@…` | Mail senders/recipients |
| `RING_MAILHOST` | `localhost` | SMTP relay |
| `RING_ANSIBLEDIR` | `/var/ring/ring-ansible` | ansible deploy target |
| `RING_ANSIBLE_HOSTFILE` / `HOSTKEYFILE` / `USERFILE` | `nodes`, `roles/etcfiles/…`, `roles/users/…` | Generated inventory files |
| `RING_ANSIBLE_KEYORIGIN` / `KEYBASE` / `KEYDIR` | `auth.infra.ring.nlnog.net:/opt/keys` … | rsync source for user keys |
| `RING_WEBDIR` | `/var/ring/ring-web` | web post publish target |
| `RING_WEB_POSTDIR` / `LOGODIR` | `content/post`, `content/images/ring-logos` | Hugo post + logo location |
| `RING_GEOCODE_URL` | nominatim reverse-geocode | City lookup for the machine list |

## Semantics ported from the legacy tool

- NULL booleans mean "off" (`active`, `alive_v4`, … are `BooleanField(null=True)`).
- `machines.hostname` is the FQDN; `ansible`/`health` hostnames are short names.
  Join the two via `short_hostname` / `ring.zone.short()`.
- Auto-deactivation is skipped when more than 10 nodes go missing in one run,
  same for the IPv4/IPv6 health updates (guards inherited from `ansible_process`).
- Ring users (the `users` table) are separate from Django `auth.User` accounts
  used to protect the admin/API. `RingUser.django_user` links the two 1:1 when
  a web/API account exists. Self-service access requests create an **inactive**
  `auth.User` + a `RingSignup` row; once a ring admin approves it the account
  is activated, linked to (or paired with) the matching `RingUser`, and given
  a DRF token.

## Development

```sh
.venv/bin/python manage.py test ring   # API authz/scoping, signup flow, nodestatus transitions
.venv/bin/python manage.py check       # system / URL check
```

When editing models, `makemigrations` then `migrate`.

## Project layout

- `ring/` — Django app: `models.py`, `api/` (DRF), `services/`
  (`nodestatus`, `ansiblefiles`, `deploy`, `mail`, `webpost`, `geocoding`),
  `management/commands/`, `templates/ring/`, `tests/`.
- `ringweb/` — project config: settings (`RING_*` block), URLs, WSGI/ASGI.
- `ring-admin.py` — the legacy reference CLI. **Not runnable**; do not try to
  execute it (imports `MySQLdb`, uses Py2-only `raw_input`, hardcodes MySQL +
  `/var/ring/` paths). It's kept as documentation of the domain semantics.
- `schema.sql` — placeholder; the real SCHEMA is embedded in `ring-admin.py`.