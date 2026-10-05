import json
import os
import tempfile

from .config import config_dir, credentials_path


def save_credentials(creds: dict) -> None:
    """Write the credentials file readable by this user only (0600, in a 0700 directory)."""
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".credentials-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(creds, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, credentials_path())
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_credentials() -> dict | None:
    try:
        creds = json.loads(credentials_path().read_text())
    except (FileNotFoundError, ValueError):
        return None
    required = ("email", "access_token", "id_token", "refresh_token", "expires_at")
    return creds if isinstance(creds, dict) and all(k in creds for k in required) else None


def clear_credentials() -> bool:
    try:
        credentials_path().unlink()
        return True
    except FileNotFoundError:
        return False
