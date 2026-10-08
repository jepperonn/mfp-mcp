"""Live smoke test against the real MyFitnessPal. Skipped unless you opt in.

    MFP_LIVE_COOKIES=~/mfp-cookie.txt pytest tests/live -v

The file holds your browser's `cookie:` header. The test writes only to the diary date below (far in the past),
creates one private custom food, and removes everything again, even when an assertion fails.
Run it when something breaks: the first failing step tells you which MyFitnessPal contract changed.
"""

import os
from pathlib import Path

import pytest

from mfp_mcp.mfp import Mfp, parse_cookie_header
from mfp_mcp.store import Store

COOKIE_FILE = os.environ.get("MFP_LIVE_COOKIES")
DATE = "2001-01-01"

pytestmark = pytest.mark.skipif(not COOKIE_FILE, reason="set MFP_LIVE_COOKIES to a cookie-header file to run live tests")


@pytest.fixture
async def mfp(tmp_path):
    client = Mfp(Store(tmp_path / "live.db", "live-test"))
    client.set_cookies(parse_cookie_header(Path(COOKIE_FILE).expanduser().read_text()))
    yield client
    await client._http.aclose()


async def test_token_and_keepalive(mfp):
    data = await mfp.refresh()
    assert data["access_token"] and data["user_id"]
    assert mfp.store.meta_get("session_expires"), "auth_token no longer renews last_login_date"
    assert await mfp.username()


async def test_read_goals_and_diary(mfp):
    assert await mfp.goals(DATE) is not None
    assert isinstance(await mfp.entries(DATE), list)


async def test_search_and_food_lookup(mfp):
    results = await mfp.search("banana")
    assert results, "web search returned nothing (markup changed?)"
    food = await mfp.food(results[0]["food_id"])
    assert food["version"] and food["serving_sizes"]


async def test_write_roundtrip(mfp):
    before = {e["id"] for e in await mfp.entries(DATE)}
    food = None
    try:
        food = await mfp.create_food("ZZ mfp-mcp live test", "serving", 100, 10, 10, 1)
        entry = await mfp.add_entry(DATE, "snacks", food, 0, 1.0)
        assert entry, "POST /v2/diary did not create an entry"
        assert round(entry["nutritional_contents"]["energy"]["value"]) == 100
        await mfp.edit_entry(entry["id"], 2.0)
        edited = next(e for e in await mfp.entries(DATE) if e["id"] == entry["id"])
        assert edited["servings"] == 2.0
    finally:
        for e in await mfp.entries(DATE):
            if e["id"] not in before:
                await mfp.delete_entry(e["id"])
        if food:
            await mfp.api("DELETE", f"/v2/foods/{food['id']}")
    assert {e["id"] for e in await mfp.entries(DATE)} == before
