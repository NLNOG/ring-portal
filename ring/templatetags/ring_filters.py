from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def ago(value):
    if not value:
        return "—"
    seconds = int((timezone.now() - value).total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return "%dm ago" % minutes
    hours = minutes // 60
    if hours < 24:
        return "%dh ago" % hours
    days = hours // 24
    if days <= 30:
        return "%dd ago" % days
    months = days // 30
    if months < 12:
        return "%dmo ago" % months
    return "%dy ago" % (months // 12)


@register.filter
def days_ago(value):
    if not value:
        return None
    seconds = int((timezone.now() - value).total_seconds())
    if seconds < 0:
        return "in the future"
    days = seconds // 86400
    if days < 1:
        hours = seconds // 3600
        if hours < 1:
            return "less than an hour ago"
        return "%dh ago" % hours
    return "%d day%s ago" % (days, "" if days == 1 else "s")


@register.filter
def get_item(obj, key):
    """Look up a dict value / attribute by a variable key."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


@register.filter
def flag(country_code):
    """Map an ISO 3166-1 alpha-2 code to its regional indicator flag emoji."""
    code = (country_code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(ord(c) + 0x1F1E6 - ord("A")) for c in code)