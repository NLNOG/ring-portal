# Deploying under Apache2 + mod_wsgi

The app is a standard Django 5.2 project (`ringweb`), so the battle-tested way to
serve it from Apache2 is **WSGI via `mod_wsgi`** in daemon mode against the
project's own venv. The repo already provides `ringweb/wsgi.py` (the WSGI
callable) and `ringweb/settings.py.example` (the config template).

> `settings.py` is your **local, gitignored** working copy — copy the example to
> it and fill in the real values. Never commit secrets.

## 1. Environment

Everything sensitive or environment-specific is read from the process env at
startup:

| Var | Purpose |
|---|---|
| `SECRET_KEY` | Session/CSRF signing. **A real, random value in production** (the committed default is `django-insecure-...`). |
| `ALLOWED_HOSTS` | Comma-separated hostnames accepted on `Host:` (currently `[]`). |
| `DEBUG` | Must be `False` in production. |
| `DB_NAME`/`DB_USER`/`DB_PASSWORD`/`DB_HOST`/`DB_PORT` | Optional read-only `legacy` MySQL (production ring data). `RING_LEGACY_DB = 1` is already in the settings, so the alias is active whenever these are set. |
| `PDB_CLIENT_ID`/`PDB_CLIENT_SECRET`/`PDB_REDIRECT_URL` | PeeringDB OAuth (see `docs/peeringdb-oauth.md`). |
| `RING_*` | Filesystem paths for the `/var/ring` management commands (below). |

These are **not** hardcoded in the settings file, so point the WSGI daemon or a
`/etc/ring/ring.env`-style environment file at them.

## 2. Virtualenv

Use one consistent Python (3.12 — the same as the app). The local `.venv` mixes
3.12 (app) and 3.14 (pip); a production venv must not. On the server:

```sh
python3.12 -m venv /srv/ring/.venv
/srv/ring/.venv/bin/pip install mod_wsgi django djangorestframework pymysql
```

Install `mod_wsgi` **inside** the venv so Apache loads the compiled module for
the same Python the app runs under:

```sh
/srv/ring/.venv/bin/mod_wsgi-express module-config
# -> LoadModule wsgi_module "/srv/ring/.venv/lib/python3.12/site-packages/mod_wsgi/server/mod_wsgi-py312.cpython-312-darwin.so"   (path varies)
```

## 3. Settings

Copy the example as your working config and adjust for production:

```sh
cp ringweb/settings.py.example ringweb/settings.py
# set: SECRET_KEY (env), ALLOWED_HOSTS, DEBUG=False
# keep: DATABASES (SQLite default + optional legacy alias)
```

Decide where the SQLite file lives. By default it is `BASE_DIR/db.sqlite3`
(WAL mode). It must be writable by the WSGI worker user (`www-data` unless you
run a dedicated user) — or move it somewhere like `/srv/ring/var/db.sqlite3`
and adjust `DATABASES["default"]["NAME"]` in your local settings. A MySQL
`default` (instead of SQLite) is also viable and scales better.

Static files: `STATIC_ROOT` is defined (`BASE_DIR/staticfiles`). Collect once
after each deploy, then serve from Apache:

```sh
/srv/ring/.venv/bin/python manage.py collectstatic --noinput
```

(The app's own templates are inline-styled and Chart.js comes from a CDN, so
the only real static payload is Django admin's assets.)

## 4. Apache vhost

```apache
LoadModule wsgi_module "/srv/ring/.venv/lib/python3.12/.../mod_wsgi.so"   # from module-config

WSGIDaemonProcess ring \
    python-home=/srv/ring/.venv \
    python-path=/srv/ring \
    user=ringapp group=ringapp \
    processes=2 threads=8 \
    env=DJANGO_SETTINGS_MODULE=ringweb.settings \
    env=SECRET_KEY=... \
    env=ALLOWED_HOSTS=ring.example.net \
    env=DEBUG=False \
    env=DB_NAME=ring env=DB_USER=ring_ro env=DB_PASSWORD=... \
    env=DB_HOST=db01.example.net env=DB_PORT=3306 \
    env=PDB_CLIENT_ID=... env=PDB_CLIENT_SECRET=... \
    env=PDB_REDIRECT_URL=https://ring.example.net/accounts/peeringdb/callback/

WSGIScriptAlias / /srv/ring/ringweb/wsgi.py process-group=ring application-group=%{GLOBAL}

<Directory /srv/ring/ringweb>
    Require all granted
</Directory>

Alias /static/ /srv/ring/staticfiles/
<Directory /srv/ring/staticfiles>
    Require all granted
</Directory>

# TLS (required for the login cookie and for PeeringDB OAuth):
# enable mod_ssl, listen on 443, and set
#   SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
#   SECURE_SSL_REDIRECT     = True
# optionally SECURE_HSTS_SECONDS etc. in settings.
```

Notes:

- `WSGIScriptAlias` to `ringweb/wsgi.py` only works if the venv/Python and the
  WSGI daemon share the module layout — keep `python-path=/srv/ring` so
  `ringweb` is importable.
- `application-group=%{GLOBAL}` couples the process-group to Apache's global
  interpreter; remove it if you run multiple apps and need isolation.
- A **SELECT-only** MySQL account is a hard requirement for the `legacy` alias —
  prove it with `ring_dbreadonly_check` before going live.

## 5. Background commands (cron/systemd)

Dashboards, the API and webchat never touch the filesystem, but several
management commands must run on a schedule (all need the same `DB_*` env):

```sh
# every 5 minutes — process ansible/health status, optionally mail on failures
*/5 * * * *   /srv/ring/.venv/bin/python /srv/ring/manage.py ring_process_status --send

# hourly — purge dead machines
0 * * * *     /srv/ring/.venv/bin/python /srv/ring/manage.py ring_purge_machines

# daily — scan hostkeys, regenerate ansible/web files, mail, DNS commands
15 3 * * *    /srv/ring/.venv/bin/python /srv/ring/manage.py ring_scan_hostkeys
30 3 * * *    /srv/ring/.venv/bin/python /srv/ring/manage.py ring_ansible_deploy
45 3 * * *    /srv/ring/.venv/bin/python /srv/ring/manage.py ring_generate_webpost --publish
0  4 * * *    /srv/ring/.venv/bin/python /srv/ring/manage.py ring_generate_mail
15 4 * * *    /srv/ring/.venv/bin/python /srv/ring/manage.py ring_dnscommands
```

- `ring_ansible_deploy`, `ring_generate_webpost --publish`,
  `ring_generate_mail` and `ring_dnscommands` need the `RING_*` paths
  (`/var/ring/ring-ansible`, `/var/ring/ring-web`, ...) present and writable.
- By default live mode is **read-only against the `legacy` MySQL** — writes from
  `ring_process_status`/`ring_purge_machines`/`ring_scan_hostkeys`/`ring_seed`
  raise `LegacyReadonlyError` unless `RING_LEGACY_WRITE_ENABLED=1` (dev-only
  escape hatch). Decide deliberately whether the production MySQL should be
  written to at all.
- Do **not** run `ring_seed` in production — it exists to load a `ring.sql` dump
  into the SQLite mirror for dev/test.

## 6. Post-deploy checklist

1. `ring_dbreadonly_check` passes against the `legacy` account.
2. `manage.py check --deploy` reports no critical issues.
3. Anonymous `GET /` shows the "Login required" page; everything else
   redirects to `/accounts/login/`.
4. `collectstatic` ran and `/static/` serves Django admin assets.
5. PeeringDB button renders and the callback round-trips over HTTPS.
6. Cron jobs logged; watch for `LegacyReadonlyError` if writes were expected.