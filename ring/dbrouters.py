"""Database router for the read-only legacy MySQL source.

Group A models (the nine legacy ``ring`` tables) read from the ``legacy``
alias whenever it is configured (``RING_LEGACY_DB=1`` / ``DB_ENGINE=mysql``).
Without that alias the router degenerates to single-database behavior so the
SQLite mirror (dev/tests) works unchanged.

The ``legacy`` database is never migrated and never schema-created by Django
(``allow_migrate`` denies everything); writes that do reach it fail loudly at
the database because the production account is SELECT-only.
"""

from django.conf import settings

LEGACY_MODELS = {
    "participant",
    "ringuser",
    "machine",
    "sshkey",
    "sshhostkey",
    "participantremark",
    "machineremark",
    "ansiblerun",
    "healthreport",
}


class RingRouter:
    def _legacy_active(self):
        return "legacy" in settings.DATABASES

    def _is_legacy(self, model):
        if not self._legacy_active():
            return False
        return (
            model._meta.app_label == "ring"
            and model._meta.model_name in LEGACY_MODELS
        )

    def db_for_read(self, model, **hints):
        if self._is_legacy(model):
            return "legacy"
        return None

    def db_for_write(self, model, **hints):
        if self._is_legacy(model):
            return "legacy"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == "legacy":
            return False
        return None