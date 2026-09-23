"""Test package.

The test suite exercises the SQLite mirror (Group A legacy tables live in the
test database). If the ``legacy`` MySQL alias is configured (live mode), strip
it before the runner builds the test databases so tests stay isolated from the
production database.
"""

import django.conf
from django.db import connections

if "legacy" in django.conf.settings.DATABASES:
    django.conf.settings.DATABASES.pop("legacy")
    django.conf.settings.DATABASE_ROUTERS = []
    connections.close_all()