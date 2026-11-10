"""Use an isolated temporary Whist home for tests, before importing the app."""
import os
import tempfile

_test_home = tempfile.TemporaryDirectory(prefix='whist-test-')
os.environ['WHIST_HOME'] = _test_home.name
from whistapp.cli import initialize
initialize('admin', 'a$$word')

import base64  # noqa: E402
import json  # noqa: E402

from starlette.testclient import TestClient  # noqa: E402

NO_CSRF = "X-Test-No-Csrf"  # a test that sends its own token, or none, sets this header


def session_csrf(client):
    """The CSRF token in the client's signed session cookie (signed, not encrypted, so readable)."""
    cookie = client.cookies.get("whist_session")
    if not cookie:
        return None
    payload = cookie.split(".", 1)[0]
    return json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4))).get("csrf")


_request = TestClient.request


def _request_with_csrf(self, method, url, *args, headers=None, **kwargs):
    """Like a browser page: every state-changing request carries the session's token as a header."""
    headers = dict(headers or {})
    if method.upper() not in ("GET", "HEAD", "OPTIONS"):
        if headers.pop(NO_CSRF, None) is None and "X-CSRF-Token" not in headers:
            token = session_csrf(self)
            if token:
                headers["X-CSRF-Token"] = token
    return _request(self, method, url, *args, headers=headers, **kwargs)


TestClient.request = _request_with_csrf
