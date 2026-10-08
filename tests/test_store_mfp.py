import time

import pytest
from conftest import COOKIES
from fake_mfp import FakeMfp

from mfp_mcp.mfp import Mfp, SessionLost, parse_cookie_header, parse_search_results, parse_set_cookie
from mfp_mcp.store import Store


def test_secrets_are_encrypted_and_need_the_right_key(tmp_path):
    path = tmp_path / "x.db"
    s = Store(path, "key-1")
    s.secret_set("mfp_cookies", {"session": "SUPERSECRETVALUE"})
    assert b"SUPERSECRETVALUE" not in path.read_bytes() and b"SUPERSECRETVALUE" not in (tmp_path / "x.db-wal").read_bytes()
    assert Store(path, "key-1").secret_get("mfp_cookies") == {"session": "SUPERSECRETVALUE"}
    assert Store(path, "other-key").secret_get("mfp_cookies") is None


def test_tokens_stored_hashed_and_survive_reopen(tmp_path):
    s = Store(tmp_path / "x.db", "k")
    s.token_put("plain-token", "access", "c1", "{}", time.time() + 60, "fam")
    s2 = Store(tmp_path / "x.db", "k")
    assert s2.token_get("plain-token", "access")["client_id"] == "c1"
    assert b"plain-token" not in (tmp_path / "x.db").read_bytes()
    s2.token_put("old", "access", "c1", "{}", time.time() - 1, "fam")
    assert s2.token_get("old", "access") is None


def test_parse_cookie_header():
    assert parse_cookie_header("Cookie: a=1; b=x=y;  c=3") == {"a": "1", "b": "x=y", "c": "3"}


def test_parse_set_cookie():
    assert parse_set_cookie("a=b; Path=/") == ("a", "b", None)
    assert parse_set_cookie("a=b; Max-Age=0")[1] is None
    assert parse_set_cookie("a=; Path=/")[1] is None
    assert parse_set_cookie("a=b; expires=Thu, 01 Jan 1970 00:00:00 GMT")[1] is None
    name, value, exp = parse_set_cookie("last=1; expires=Sat, 07 Nov 2099 17:31:55 GMT")
    assert value == "1" and exp > time.time()


async def test_refresh_renews_and_saves_cookies_and_drops_deleted(tmp_path):
    store, fake = Store(tmp_path / "x.db", "k"), FakeMfp()
    mfp = Mfp(store, fake.transport())
    mfp.set_cookies(parse_cookie_header(COOKIES))
    await mfp.refresh()
    saved = store.secret_get("mfp_cookies")
    assert saved["_mfp_session"] == "renewed" and saved["last_login_date"] == "2026-11-07"
    assert "__Secure-next-auth.session-token" not in saved  # Max-Age=0 cookie is never persisted
    assert store.meta_get("session_expires").startswith("2099-11-07")
    assert store.meta_get("last_keepalive")
    # the next request sends the renewed cookie, not the old one
    await mfp.refresh()
    assert "_mfp_session=renewed" in fake.cookies_seen[-1] and "session-token" not in fake.cookies_seen[-1]


async def test_session_lost_when_redirected_to_login(tmp_path):
    fake = FakeMfp()
    fake.logged_in = False
    mfp = Mfp(Store(tmp_path / "x.db", "k"), fake.transport())
    mfp.set_cookies(parse_cookie_header(COOKIES))
    with pytest.raises(SessionLost):
        await mfp.refresh()


async def test_cookies_persist_across_restart(tmp_path):
    store = Store(tmp_path / "x.db", "k")
    a = Mfp(store, FakeMfp().transport())
    a.set_cookies(parse_cookie_header(COOKIES))
    await a.refresh()
    b = Mfp(Store(tmp_path / "x.db", "k"), FakeMfp().transport())
    assert b.connected and b.cookies["_mfp_session"] == "renewed"


async def test_bearer_expiry_triggers_refresh(tmp_path):
    fake = FakeMfp()
    mfp = Mfp(Store(tmp_path / "x.db", "k"), fake.transport())
    mfp.set_cookies(parse_cookie_header(COOKIES))
    await mfp.entries("2026-01-01")
    await mfp.entries("2026-01-01")
    assert sum(1 for m, p in fake.requests if p == "/user/auth_token") == 1
    mfp._token_exp = 0
    await mfp.entries("2026-01-01")
    assert sum(1 for m, p in fake.requests if p == "/user/auth_token") == 2


def test_parse_search_results():
    from fake_mfp import SEARCH_HTML

    res = parse_search_results(SEARCH_HTML)
    assert res[0] == {"food_id": "555", "name": "Banana", "brand": "Generic", "serving": "medium", "kcal": 105.0, "verified": True}
    assert res[1]["brand"] == "Rema 1000, Rema" and res[1]["serving"] == "100 g" and res[1]["kcal"] == 89.0
