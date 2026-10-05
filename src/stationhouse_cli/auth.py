import base64
import json
import re
import time
import urllib.error
import urllib.request

from . import config
from .store import load_credentials, save_credentials

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
REFRESH_MARGIN_SECONDS = 120


class AuthError(Exception):
    pass


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_PATTERN.match(email):
        raise AuthError("Enter the email address of your Station House account.")
    return email


def _expiry(access_token: str) -> float:
    """The access token's `exp`, read only to schedule a refresh. The platform verifies the token itself."""
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return 0.0


def _credentials(email: str, tokens: dict, refresh_token: str) -> dict:
    return {"email": email, "access_token": tokens["access_token"], "id_token": tokens["id_token"],
            "refresh_token": refresh_token, "expires_at": _expiry(tokens["access_token"])}


def login(email: str, password: str) -> dict:
    """Sign in to the Station House Cognito pool with SRP (the password is never sent) and store the result."""
    email = normalize_email(email)
    from pycognito import Cognito
    from pycognito.exceptions import ForceChangePasswordException

    user = Cognito(config.USER_POOL_ID, config.CLIENT_ID, user_pool_region=config.REGION, username=email)
    try:
        user.authenticate(password=password)
    except ForceChangePasswordException:
        raise AuthError("This account must set a new password first. Sign in on the Station House website.") from None
    except Exception as e:  # botocore ClientError: wrong password, unconfirmed account, unknown user
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if code in ("NotAuthorizedException", "UserNotFoundException"):
            raise AuthError("Wrong email or password.") from None
        if code == "UserNotConfirmedException":
            raise AuthError("This account's email isn't confirmed yet. Confirm it on the Station House website.") from None
        raise AuthError(f"Couldn't sign in: {code or type(e).__name__}") from None
    creds = _credentials(email, {"access_token": user.access_token, "id_token": user.id_token}, user.refresh_token)
    save_credentials(creds)
    return creds


def _cognito_post(target: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"https://cognito-idp.{config.REGION}.amazonaws.com/", method="POST", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": f"AWSCognitoIdentityProviderService.{target}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def refresh(creds: dict) -> dict:
    try:
        out = _cognito_post("InitiateAuth", {
            "AuthFlow": "REFRESH_TOKEN_AUTH", "ClientId": config.CLIENT_ID,
            "AuthParameters": {"REFRESH_TOKEN": creds["refresh_token"]}})["AuthenticationResult"]
    except urllib.error.HTTPError:
        raise AuthError("Your session has expired. Run `stationhouse login`.") from None
    except (urllib.error.URLError, KeyError, ValueError) as e:
        raise AuthError(f"Couldn't refresh your session: {e!r}") from None
    tokens = {"access_token": out["AccessToken"], "id_token": out["IdToken"]}
    new = _credentials(creds["email"], tokens, out.get("RefreshToken", creds["refresh_token"]))
    save_credentials(new)
    return new


def current_credentials() -> dict:
    """The stored credentials with a fresh access token, or AuthError telling the user to log in."""
    creds = load_credentials()
    if creds is None:
        raise AuthError("You aren't signed in. Run `stationhouse login`.")
    if creds["expires_at"] - time.time() < REFRESH_MARGIN_SECONDS:
        creds = refresh(creds)
    return creds
