from conftest import make_client, mcp_call, oauth_login


def test_unauthenticated_mcp_gets_401_with_resource_metadata(env):
    client, *_ = env
    r = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={"Accept": "application/json, text/event-stream"}
    )
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]


def test_discovery_documents(env):
    client, *_ = env
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["issuer"].rstrip("/") == "https://mfp.example.com" and meta["registration_endpoint"].endswith("/register")
    assert "S256" in meta["code_challenge_methods_supported"]
    prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"].rstrip("/").endswith("/mcp")


def test_full_flow_and_tool_listing(env):
    client, *_ = env
    tok, _ = oauth_login(client)
    r = mcp_call(client, tok["access_token"], "tools/list")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {
        "get_day",
        "search_food",
        "log_food",
        "edit_entry",
        "delete_entry",
        "my_foods_list",
        "my_foods_save",
        "my_foods_delete",
        "status",
    } <= names


def test_wrong_passcode_rejected_then_locked_out(env):
    client, *_ = env
    for _ in range(5):
        tok, resp = oauth_login(client, passcode="nope")
        assert tok is None and resp.status_code == 401
    tok, resp = oauth_login(client, passcode="hunter2")  # even the right one is blocked after 5 failures
    assert tok is None and resp.status_code == 429


def test_login_survives_restart_without_passcode(config, fake):
    client, mfp, store = make_client(config, fake)
    with client:
        tok, _ = oauth_login(client)
    client2, *_ = make_client(config, fake)  # brand-new app + connections, same database file
    with client2:
        assert mcp_call(client2, tok["access_token"], "tools/list").status_code == 200
        r = client2.post(
            "/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": tok["client_id"]}
        )
        assert r.status_code == 200
        new = r.json()
        assert new["refresh_token"] != tok["refresh_token"] and new["access_token"] != tok["access_token"]
        assert mcp_call(client2, new["access_token"], "tools/list").status_code == 200


def test_refresh_token_rotation_has_short_grace_then_dies(env):
    client, _, store = env
    tok, _ = oauth_login(client)
    data = {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": tok["client_id"]}
    assert client.post("/token", data=data).status_code == 200
    assert client.post("/token", data=data).status_code == 200  # within grace (lost-response retry)
    import time

    row = store.token_get(tok["refresh_token"], "refresh")
    assert row["expires_at"] < time.time() + 130  # grace is short, not 180 days


def test_garbage_and_revoked_tokens_rejected(env):
    client, *_ = env
    assert mcp_call(client, "garbage", "tools/list").status_code == 401
    tok, _ = oauth_login(client)
    assert (
        client.post("/revoke", data={"token": tok["access_token"], "client_id": tok["client_id"], "client_secret": ""}).status_code == 200
    )
    assert mcp_call(client, tok["access_token"], "tools/list").status_code == 401


def test_wrong_host_rejected(env):
    client, *_ = env
    tok, _ = oauth_login(client)
    r = client.post(
        "/mcp",
        headers={
            "Host": "evil.example.org",
            "Authorization": f"Bearer {tok['access_token']}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert r.status_code in (400, 421)
