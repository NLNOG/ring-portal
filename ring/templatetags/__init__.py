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
    if days < 30:
        return "%dd ago" % days
    months = days // 30
    if months < 12:
        return "%dmo ago" % months
    return "%dy ago" % (months // 12)