import functools
import json
import unicodedata
import urllib.request

from django.conf import settings

import pycountry


@functools.lru_cache(maxsize=4096)
def city_from_geo(geo):
    """Reverse-geocode a 'lat,lon' string to a city name (best effort)."""
    try:
        lat, lon = geo.split(",")
        url = settings.RING_GEOCODE_URL + "lat=" + lat.strip() + "&lon=" + lon.strip()
        response = json.load(urllib.request.urlopen(url, timeout=10))
        address = response["address"]
        city = address.get("city") or address.get("village")
        if city:
            if isinstance(city, str):
                return city
            return str(unicodedata.normalize("NFD", city).encode("ascii", "ignore"))
    except Exception:
        pass
    return ""


def countryname(country, state=None):
    """Pretty-print a country (and optional subdivision) alpha-2 code."""
    cname = ""
    try:
        cname = pycountry.countries.get(alpha_2=country).name
    except (KeyError, AttributeError):
        cname = "Unknown"
    if state:
        try:
            subdivision = pycountry.subdivisions.get(code="%s-%s" % (country, state))
            cname = "%s, %s" % (subdivision.name, cname)
        except (KeyError, AttributeError):
            cname = "Unknown, %s" % cname
    return cname