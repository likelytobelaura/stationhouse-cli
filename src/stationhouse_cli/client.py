import json
import urllib.error
import urllib.parse
import urllib.request

from . import __version__, config
from .auth import current_credentials


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def request(method: str, path: str, body: dict | None = None, query: dict | None = None):
    """Call the Station House API as the signed-in member. The access token authorizes the call; the
    ID token lets the platform read the member's verified email. Identity is never taken from the body."""
    creds = current_credentials()
    url = config.api_url() + path + ("?" + urllib.parse.urlencode(query) if query else "")
    req = urllib.request.Request(
        url, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {creds['access_token']}",
                 "x-station-house-id-token": creds["id_token"],
                 "Content-Type": "application/json", "User-Agent": f"stationhouse-cli/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        raise ApiError(_message(e), e.code) from None
    except urllib.error.URLError as e:
        raise ApiError(f"Couldn't reach {config.api_url()}: {e.reason}") from None


def _message(e: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(e.read())
        text = body.get("error") or body.get("message")
        if isinstance(text, dict):
            text = text.get("message")
    except (ValueError, AttributeError):
        text = None
    if e.code == 401:
        return text or "Not signed in, or your session expired. Run `stationhouse login`."
    if e.code == 404 and not text:
        return "This Station House server doesn't support that yet."
    return text or f"The server answered {e.code}."
