import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


def jwt(exp: float) -> str:
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'none'})}.{b64({'exp': exp})}.sig"


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("STATIONHOUSE_API_URL", raising=False)


@pytest.fixture
def signed_in():
    from stationhouse_cli.store import save_credentials
    creds = {"email": "laura@example.com", "access_token": jwt(time.time() + 3600), "id_token": "id.tok.en",
             "refresh_token": "refresh", "expires_at": time.time() + 3600}
    save_credentials(creds)
    return creds


class FakeApi:
    """Records requests and answers from `routes[(method, path)] = (status, body)`."""

    def __init__(self):
        self.routes, self.calls = {}, []
        api = self

        class H(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                path = self.path.split("?")[0]
                api.calls.append({"method": self.command, "path": self.path, "body": body, "headers": dict(self.headers)})
                status, out = api.routes.get((self.command, path), (404, {}))
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = do_POST = _handle

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setenv("STATIONHOUSE_API_URL", fake.url)
    yield fake
    fake.server.shutdown()
