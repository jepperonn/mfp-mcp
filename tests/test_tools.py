import json

import pytest
from conftest import COOKIES, call_tool, oauth_login

from mfp_mcp.mfp import parse_cookie_header


@pytest.fixture
def ready(env, fake):
    client, mfp, store = env
    mfp.set_cookies(parse_cookie_header(COOKIES))
    tok, _ = oauth_login(client)
    return client, mfp, store, fake, tok["access_token"]


def payload(result):
    assert not result.get("isError"), result
    sc = result.get("structuredContent")
    if sc is not None:
        return sc.get("result", sc)
    return json.loads(result["content"][0]["text"])


def error_text(result):
    assert result.get("isError"), result
    return result["content"][0]["text"]


def test_my_foods_save_log_one_step_edit_delete(ready):
    client, mfp, store, fake, tok = ready
    saved = payload(
        call_tool(client, tok, "my_foods_save", {"name": "5 æg", "kcal": 400, "protein": 32, "carbs": 2, "fat": 28, "aliases": ["5 eggs"]})
    )
    assert saved["saved"] == "5 æg"
    assert [f["name"] for f in payload(call_tool(client, tok, "my_foods_list"))] == ["5 æg"]
    out = payload(
        call_tool(client, tok, "log_food", {"meal": "breakfast", "items": [{"food": "5 EGGS", "servings": 1}], "date": "2026-01-05"})
    )
    assert out["logged"][0]["kcal"] == 400 and out["logged"][0]["protein"] == 32
    assert out["day_totals"]["kcal"] == 400 and out["remaining"]["kcal"] == 1600
    day = payload(call_tool(client, tok, "get_day", {"date": "2026-01-05"}))
    entry = day["meals"]["Breakfast"][0]
    payload(call_tool(client, tok, "edit_entry", {"entry_id": entry["id"], "servings": 2}))
    assert payload(call_tool(client, tok, "get_day", {"date": "2026-01-05"}))["totals"]["kcal"] == 800
    payload(call_tool(client, tok, "delete_entry", {"entry_id": entry["id"]}))
    assert payload(call_tool(client, tok, "get_day", {"date": "2026-01-05"}))["meals"] == {}
    assert payload(call_tool(client, tok, "my_foods_delete", {"name": "5 æg"}))["deleted"] == "5 æg"


def test_search_puts_my_foods_first_and_log_mfp_food_by_id(ready):
    client, mfp, store, fake, tok = ready
    call_tool(client, tok, "my_foods_save", {"name": "Banan shake", "kcal": 300, "protein": 30, "carbs": 30, "fat": 5})
    res = payload(call_tool(client, tok, "search_food", {"query": "banan"}))
    assert res["my_foods"][0]["name"] == "Banan shake" and res["mfp"][0]["food_id"] == "555"
    info = payload(call_tool(client, tok, "food_info", {"food_id": "555"}))
    assert [s["unit"] for s in info["serving_sizes"]] == ["medium", "g"]
    out = payload(call_tool(client, tok, "log_food", {"meal": "snacks", "items": [{"food": "555", "servings": 2}], "date": "2026-01-05"}))
    assert out["logged"][0]["kcal"] == 210
    out = payload(
        call_tool(
            client, tok, "log_food", {"meal": "snacks", "items": [{"food": "555", "servings": 1, "serving": 1}], "date": "2026-01-05"}
        )
    )
    assert out["logged"][0]["kcal"] == 89.2 or abs(out["logged"][0]["kcal"] - 89.2) < 0.2


def test_log_is_all_or_nothing(ready):
    client, mfp, store, fake, tok = ready
    call_tool(client, tok, "my_foods_save", {"name": "Skyr", "kcal": 100, "protein": 17, "carbs": 4, "fat": 0})
    msg = error_text(
        call_tool(client, tok, "log_food", {"meal": "lunch", "items": [{"food": "Skyr"}, {"food": "nonexistent"}], "date": "2026-01-05"})
    )
    assert "nonexistent" in msg and "search_food" in msg
    assert fake.entries == {}


def test_bad_meal_and_bad_date_are_clear_errors(ready):
    client, _, _, _, tok = ready
    assert "meal" in error_text(call_tool(client, tok, "log_food", {"meal": "brunch", "items": [{"food": "1"}]})).lower()
    assert "YYYY-MM-DD" in error_text(call_tool(client, tok, "get_day", {"date": "tomorrow"}))


def test_lost_session_points_to_setup(ready):
    client, _, _, fake, tok = ready
    fake.logged_in = False
    msg = error_text(call_tool(client, tok, "get_day", {"date": "2026-01-05"}))
    assert "https://mfp.example.com/setup" in msg
    st = payload(call_tool(client, tok, "status"))
    assert st["login_ok"] is False and "/setup" in st["problem"]


def test_status_when_healthy_and_when_not_connected(env, fake):
    client, mfp, store = env
    tok, _ = oauth_login(client)
    assert "/setup" in payload(call_tool(client, tok["access_token"], "status"))["problem"]
    mfp.set_cookies(parse_cookie_header(COOKIES))
    st = payload(call_tool(client, tok["access_token"], "status"))
    assert st["login_ok"] and st["username"] == "tester" and st["session_valid_until"].startswith("2099")
    assert "renewed" not in json.dumps(st)  # never leak cookie values


def test_setup_page_validates_and_saves_cookies(env, fake):
    client, mfp, store = env
    assert client.get("/setup").status_code == 200
    r = client.post("/setup", data={"passcode": "wrong", "cookies": COOKIES})
    assert r.status_code == 401 and not mfp.connected
    r = client.post("/setup", data={"passcode": "hunter2", "cookies": "cookie: " + COOKIES})
    assert r.status_code == 200 and "tester" in r.text and "2099" in r.text
    assert mfp.connected and store.secret_get("mfp_cookies")["_mfp_session"] == "renewed"
    assert "_mfp_session" not in r.text and "stale" not in r.text


def test_setup_rejects_dead_cookies_and_keeps_old_ones(env, fake):
    client, mfp, store = env
    mfp.set_cookies(parse_cookie_header(COOKIES))
    fake.logged_in = False
    r = client.post("/setup", data={"passcode": "hunter2", "cookies": COOKIES.replace("a=1", "a=9")})
    assert r.status_code == 400 and "did not accept" in r.text
    assert mfp.cookies["a"] == "1"  # previous cookies restored
    r = client.post("/setup", data={"passcode": "hunter2", "cookies": "a=1"})
    assert r.status_code == 400


def test_keepalive_records_error_and_recovers(env, fake):
    import asyncio

    from mfp_mcp import keepalive

    client, mfp, store = env
    mfp.set_cookies(parse_cookie_header(COOKIES))
    assert asyncio.run(keepalive.run_once(mfp, store)) is True and store.meta_get("last_error") is None
    fake.logged_in = False
    assert asyncio.run(keepalive.run_once(mfp, store)) is False and store.meta_get("last_error") == "login lost"
