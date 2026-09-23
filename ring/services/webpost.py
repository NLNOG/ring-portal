"""Web post / Hugo content generation.

Port of ring-admin.py's generate_hugopost and web_publish methods.
"""

from datetime import datetime

from django.conf import settings
from ring.services import deploy
from ring.services.mail import Template, HUGOPOST
from ring.services.geocoding import countryname
from ring.models import RingUser


def generate_hugopost(username, publish=False):
    """Build a Hugo markdown post body for a new participant joining."""
    u = RingUser.objects.get(username=username)
    p = u.participant
    mfirst = u.machines.all().first()
    if not mfirst:
        raise LookupError("no machines found for %s" % username)

    country = countryname(mfirst.country, mfirst.state or "")
    date = datetime.now().isoformat()[:10]
    text = Template(HUGOPOST)
    postbody = text.substitute(
        company=p.company,
        companydesc=p.companydesc,
        hostname=mfirst.hostname,
        autnum="AS%s" % mfirst.autnum,
        country=country,
        countrycode=mfirst.country,
        date=date,
    )
    print(postbody)
    if publish:
        print("Publishing post... ")
        deploy.web_publish(username, postbody)
        print("Done.")


def web_checkout():
    deploy.web_checkout()


def web_push():
    deploy.web_push()


def web_publish(username, postbody):
    deploy.web_publish(username, postbody)