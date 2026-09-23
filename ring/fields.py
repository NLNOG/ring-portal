"""Custom model fields.

``EpochDateTimeField`` bridges the legacy ring MySQL schema, which stores
``tstamp``/``last_active`` as INTEGER unix epochs, and the DateTimeField
semantics the rest of the tool uses. The Python side always sees a timezone
aware datetime; the database column holds an integer epoch.
"""

import datetime

from django.db import models
from django.utils import timezone

UTC = datetime.timezone.utc


class EpochDateTimeField(models.DateTimeField):
    """A DateTimeField persisting as an INTEGER unix epoch."""

    def db_type(self, connection):
        return "INTEGER"

    def get_internal_type(self):
        # Avoid the backend's DateTime converter: the legacy column holds an
        # INTEGER epoch, not a serialized timestamp string.
        return "BigIntegerField"

    def from_db_value(self, value, expression, connection):
        if value is None or value == 0:
            return None
        if isinstance(value, datetime.datetime):
            if timezone.is_naive(value):
                return timezone.make_aware(value, UTC)
            return value
        try:
            return datetime.datetime.fromtimestamp(int(value), tz=UTC)
        except (ValueError, OverflowError, OSError):
            return None

    def convert_value(self, value, expression, connection):
        return self.from_db_value(value, expression, connection)

    def get_db_prep_value(self, value, connection, prepared=False):
        if not prepared:
            value = self.get_prep_value(value)
        if value is None:
            return None
        if timezone.is_naive(value):
            value = timezone.make_aware(value, UTC)
        return int(value.timestamp())