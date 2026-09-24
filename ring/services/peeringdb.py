import json
import urllib.parse
import urllib.request

from django.conf import settings


class PDBError(Exception):
    """Raised when a PeeringDB OAuth step fails."""


def authorize_url(state):
    params = urllib.parse.urlencode(
        {
            "client_id": settings.PDB_CLIENT_ID,
            "redirect_uri": settings.PDB_REDIRECT_URL,
            "response_type": "code",
            "state": state,
            "scope": "profile email networks",
        }
    )
    return settings.PDB_ENDPOINT + "oauth2/authorize/?" + params


def exchange_code(code):
    """Trade an authorization code for an access token."""
    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.PDB_REDIRECT_URL,
            "client_id": settings.PDB_CLIENT_ID,
            "client_secret": settings.PDB_CLIENT_SECRET,
        }
    ).encode("utf-8")
    response = _post(settings.PDB_ENDPOINT + "oauth2/token/", data)
    token = response.get("access_token")
    if not token:
        raise PDBError("PeeringDB token response missing access_token")
    return token


def fetch_profile(access_token):
    """Fetch the authenticated user's profile from /profile/v1."""
    request = urllib.request.Request(
        settings.PDB_ENDPOINT + "profile/v1",
        headers={
            "Authorization": "Bearer %s" % access_token,
            "User-Agent": "ring-portal/ring",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except Exception as exc:
        raise PDBError("Unable to fetch PeeringDB profile: %s" % exc) from exc


def _post(url, data):
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "ring-portal/ring",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise PDBError("PeeringDB token error %s: %s" % (exc.code, body)) from exc
    except Exception as exc:
        raise PDBError("PeeringDB token error: %s" % exc) from exc