"""The login rate limit counts per visitor behind the tunnel, not for everybody together."""
import re
from pathlib import Path

from starlette.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from whistapp import auth
from whistapp.main import app

TUNNEL = "172.18.0.3"  # the cloudflared container on the Docker network
GOOD = {"username": "admin", "password": "a$$word"}
BAD = {"username": "admin", "password": "wrong"}


def _compose_trusted():
    text = (Path(__file__).parent.parent / "compose.yaml").read_text(encoding="utf-8")
    return re.search(r'FORWARDED_ALLOW_IPS: "([^"]+)"', text).group(1)


def _login(app_, data, forwarded_for=None, client=TUNNEL):
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    with TestClient(app_, client=(client, 50000)) as c:
        return c.post("/login", data=data, headers=headers, follow_redirects=False).status_code


def test_one_visitors_failed_logins_do_not_lock_the_others_out():
    """As uvicorn serves the app in Docker: compose.yaml's FORWARDED_ALLOW_IPS trusts the tunnel."""
    served = ProxyHeadersMiddleware(app, trusted_hosts=_compose_trusted())
    auth._attempts.clear()
    try:
        for _ in range(10): _login(served, BAD, "203.0.113.7")
        # The attacker is locked out, even with the right password ...
        assert _login(served, GOOD, "203.0.113.7") == 200
        # ... and cannot get a fresh count by sending a made-up address first: Cloudflare appends the real one.
        assert _login(served, GOOD, "198.51.100.1, 203.0.113.7") == 200
        # Another visitor logs in as usual.
        assert _login(served, GOOD, "198.51.100.2") == 303
    finally:
        auth._attempts.clear()


def test_forwarded_header_is_ignored_from_a_public_address():
    """Reached directly (not through the tunnel), a visitor cannot pick their own rate-limit key."""
    served = ProxyHeadersMiddleware(app, trusted_hosts=_compose_trusted())
    auth._attempts.clear()
    try:
        for i in range(10): _login(served, BAD, f"198.51.100.{i}", client="203.0.113.9")
        assert _login(served, GOOD, "198.51.100.99", client="203.0.113.9") == 200
    finally:
        auth._attempts.clear()
