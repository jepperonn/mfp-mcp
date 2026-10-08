import base64
import hashlib
import secrets

import pytest
from fake_mfp import FakeMfp
from starlette.testclient import TestClient

from mfp_mcp.config import Config
from mfp_mcp.server import create_app

COOKIES = "a=1; b=2; c=3; d=4; e=5; _mfp_session=old; __Secure-next-auth.session-token=stale"


@pytest.fixture
def fake():
    return FakeMfp()


@pytest.fixture
def config(tmp_path):
    return Config("https://mfp.example.com", "hunter2", "test-secret-key", str(tmp_path), "UTC")


def make_client(config, fake):
    app, mfp, store = create_app(config, transport=fake.transport())
    return TestClient(app, base_url="https://mfp.example.com"), mfp, store


@pytest.fixture
def env(config, fake):
    client, mfp, store = make_client(config, fake)
    with client:
        yield client, mfp, store


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def oauth_login(client, passcode="hunter2"):
    """Run the whole connector flow; return the token response JSON."""
    reg = client.post(
        "/register",
        json={
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "client_name": "Claude",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert reg.status_code in (200, 201), reg.text
    cid = reg.json()["client_id"]
    verifier, challenge = pkce()
    r = client.get(
        "/authorize",
        params={
            "client_id": cid,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "resource": "https://mfp.example.com/mcp",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 307), r.text
    login_url = r.headers["location"]
    assert "/login?req=" in login_url
    req = login_url.split("req=")[1]
    r = client.post("/login", data={"req": req, "passcode": passcode}, follow_redirects=False)
    if r.status_code != 302:
        return None, r
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(r.headers["location"]).query)
    assert qs["state"] == ["xyz"]
    tok = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": qs["code"][0],
            "client_id": cid,
            "code_verifier": verifier,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
        },
    )
    assert tok.status_code == 200, tok.text
    return {**tok.json(), "client_id": cid}, r


def mcp_call(client, access, method, params=None, id=1):
    h = {"Authorization": f"Bearer {access}", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    return client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}})


def call_tool(client, access, name, args=None):
    r = mcp_call(client, access, "tools/call", {"name": name, "arguments": args or {}})
    assert r.status_code == 200, r.text
    return r.json()["result"]
