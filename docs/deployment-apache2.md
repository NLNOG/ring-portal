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
| `SECRET_KEY` | Session/CSRF signing. Env-driven (falls back to an insecure dev default). **Set a real random value in production.** |
| `ALLOWED_HOSTS` | Comma-separated hostnames accepted on `Host:`. Falls back to `[]` (dev-local hosts only). |
| `DEBUG` | `True`/`False`. Defaults to `False`. |
| `DB_NAME`/`DB_USER`/`DB_PASSWORD`/`DB_HOST`/`DB_PORT` | Optional read-only `legacy` MySQL (production ring data). `RING_LEGACY_DB = 1` is already in the settings, so the alias is active whenever the DB is reachable. |
| `PDB_CLIENT_ID`/`PDB_CLIENT_SECRET`/`PDB_REDIRECT_URL` | PeeringDB OAuth (see `docs/peeringdb-oauth.md`). |
| `RING_*` | Filesystem paths for the `/var/ring` management commands (below). |

These are read from the process env at startup, so point the WSGI daemon or an
`/etc/ring/ring.env`-style environment file at them — do not hardcode them in a
committed settings file.

## 2. Virtualenv

Use one consistent Python (3.12 — the same as the app). The local `.venv` mixes
3.12 (app) and 3.14 (pip); a production venv must not. On the server:

```sh
python3.12 -m venv /var/www/portal.ring.nlnog.net/venv
/var/www/portal.ring.nlnog.net/venv/bin/pip install django djangorestframework pymysql
```

Install `mod_wsgi` **inside** the venv so Apache loads the compiled module for
the same Python the app runs under:

```sh
/var/www/portal.ring.nlnog.net/venv/bin/mod_wsgi-express module-config
# -> LoadModule wsgi_module "/var/www/portal.ring.nlnog.net/venv/lib/python3.12/site-packages/mod_wsgi/server/mod_wsgi-*.so"   (path varies)
```

`python-home=/var/www/portal.ring.nlnog.net/venv` on `WSGIDaemonProcess` makes
the daemon use that venv's Python; a distro-packaged `libapache2-mod-wsgi`
only works if it was built for the **same** Python version as the venv.

## 3. Deployed tree — gitignored files included

Copying the git repo alone is not enough: `ringweb/settings.py` and
`db.sqlite3` are gitignored and must be supplied on the server:

```sh
cp ringweb/settings.py.example ringweb/settings.py   # then edit for production
#   DEBUG=False (default), ALLOWED_HOSTS=["portal.ring.nlnog.net"],
#   SECRET_KEY=<real random value>  — or set these as env on the WSGI daemon.
#   Legacy MySQL: set DB_HOST/DB_NAME/DB_USER/DB_PASSWORD, or point at
#   127.0.0.1:3306 if ring runs on this box.
```

- The WSGI entrypoint is the project's own `ringweb/wsgi.py` (no separate
  `portal.wsgi` needed — `python-path` on the daemon makes `ringweb` importable).
- `db.sqlite3` (auth users, signups, PeeringDB identities) must exist in the
  deployment root, be readable/writable by the WSGI user, and be migrated:
  `python manage.py migrate`. Ship an existing one or create a fresh one +
  superuser.
- Static files: `STATIC_ROOT` is defined (`BASE_DIR/staticfiles`). Collect once
  after each deploy, then serve from Apache:

```sh
/var/www/portal.ring.nlnog.net/venv/bin/python manage.py collectstatic --noinput
```

(The app's own templates are inline-styled and Chart.js comes from a CDN, so
the only real static payload is Django admin's assets.)

## 4. Apache vhost

```apache
LoadModule wsgi_module "/var/www/portal.ring.nlnog.net/venv/lib/python3.12/.../mod_wsgi.so"   # from module-config

WSGIDaemonProcess ring_portal \
    python-home=/var/www/portal.ring.nlnog.net/venv \
    python-path=/var/www/portal.ring.nlnog.net \
    user=ringapp group=ringapp \
    processes=2 threads=8 \
    env=DJANGO_SETTINGS_MODULE=ringweb.settings \
    env=SECRET_KEY=... \
    env=ALLOWED_HOSTS=portal.ring.nlnog.net \
    env=DEBUG=False \
    env=DB_NAME=ring env=DB_USER=ring_ro env=DB_PASSWORD=... \
    env=DB_HOST=db01.example.net env=DB_PORT=3306 \
    env=PDB_CLIENT_ID=... env=PDB_CLIENT_SECRET=... \
    env=PDB_REDIRECT_URL=https://portal.ring.nlnog.net/accounts/peeringdb/callback/

WSGIScriptAlias / /var/www/portal.ring.nlnog.net/ringweb/wsgi.py process-group=ring_portal application-group=%{GLOBAL}

# Apache 2.4 syntax — the older `Order deny,allow` / `Allow from all` lines are
# Apache 2.2 and will be rejected by `apachectl configtest` on 2.4.
<Directory /var/www/portal.ring.nlnog.net>
    Require all granted
</Directory>

Alias /static/ /var/www/portal.ring.nlnog.net/staticfiles/
<Directory /var/www/portal.ring.nlnog.net/staticfiles>
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
- `python-path` is **required**: mod_wsgi only adds the WSGI script's own
  directory to `sys.path`, so a script inside `ringweb/` cannot import the
  `ringweb` package unless the project root is added here — keep it pointing at
  the deployment root.
- `application-group=%{GLOBAL}` couples the process-group to Apache's global
  interpreter; remove it if you run multiple apps and need isolation.
- A **SELECT-only** MySQL account is a hard requirement for the `legacy` alias —
  prove it with `ring_dbreadonly_check` before going live.

## 5. Background commands (cron/systemd)

Dashboards, the API and webchat never touch the filesystem, but several
management commands must run on a schedule (all need the same `DB_*` env):

```sh
# every 5 minutes — process ansible/health status, optionally mail on failures
*/5 * * * *   /var/www/portal.ring.nlnog.net/venv/bin/python /var/www/portal.ring.nlnog.net/manage.py ring_process_status --send

# hourly — purge dead machines
0 * * * *     /var/www/portal.ring.nlnog.net/venv/bin/python /var/www/portal.ring.nlnog.net/manage.py ring_purge_machines

# daily — scan hostkeys, regenerate ansible/web files, mail, DNS commands
15 3 * * *    /var/www/portal.ring.nlnog.net/venv/bin/python /var/www/portal.ring.nlnog.net/manage.py ring_scan_hostkeys
30 3 * * *    /var/www/portal.ring.nlnog.net/venv/bin/python /var/www/portal.ring.nlnog.net/manage.py ring_ansible_deploy
45 3 * * *    /var/www/portal.ring.nlnog.net/venv/bin/python /var/www/portal.ring.nlnog.net/manage.py ring_generate_webpost --publish
0  4 * * *    /var/www/portal.ring.nlnog.net/venv/bin/python /var/www/portal.ring.nlnog.net/manage.py ring_generate_mail
15 4 * * *    /var/www/portal.ring.nlnog.net/venv/bin/python /var/www/portal.ring.nlnog.net/manage.py ring_dnscommands
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