import os
from pathlib import Path

# Public identifiers of the Station House Cognito pool (the same values the web app ships in
# amplify_outputs.json). They are not secrets: a sign-in still needs a member's own password.
USER_POOL_ID = "us-east-1_IVIvCXApg"
CLIENT_ID = "79iirq0u3k2uuqhq3s2tph7ru3"
REGION = "us-east-1"
DEFAULT_API_URL = "https://app.station-house.net"
DEFAULT_PROJECT = "conspiracybench"


def api_url() -> str:
    return os.environ.get("STATIONHOUSE_API_URL", DEFAULT_API_URL).rstrip("/")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) / "stationhouse" if base else Path.home() / ".config" / "stationhouse"


def credentials_path() -> Path:
    return config_dir() / "credentials.json"
