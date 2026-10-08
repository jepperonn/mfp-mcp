"""MyFitnessPal client (unofficial).

Auth model, as observed:
  * The browser's cookies authenticate the web site. ``GET /user/auth_token?refresh=true`` with them returns a
    bearer token for ``api.myfitnesspal.com`` and renews ``last_login_date`` / ``_mfp_session`` (30 days).
  * Diary reads/writes and food lookups use the JSON API (``/v2/...``) with that bearer token.
  * Food *search* has no JSON endpoint, so it scrapes the web search page.

Every ``Set-Cookie`` is absorbed into our own cookie dict and saved (encrypted) so the session keeps renewing.
Cookies the server tells us to delete (Max-Age=0, empty, expired) are dropped, never saved.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import email.utils
import time
from typing import Any

import httpx
import lxml.html

from .store import Store

WEB = "https://www.myfitnesspal.com"
API = "https://api.myfitnesspal.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
MEALS = {"breakfast": ("Breakfast", 0), "lunch": ("Lunch", 1), "dinner": ("Dinner", 2), "snacks": ("Snacks", 3), "snack": ("Snacks", 3)}
COOKIE_SECRET = "mfp_cookies"


class SessionLost(Exception):
    """MyFitnessPal no longer accepts our cookies; the owner must paste new ones on /setup."""


class MfpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"MyFitnessPal error {status}: {message}")
        self.status = status


def parse_set_cookie(header: str) -> tuple[str, str | None, float | None]:
    """Return (name, value-or-None-if-deleted, expires_epoch-or-None)."""
    parts = [p.strip() for p in header.split(";")]
    name, _, value = parts[0].partition("=")
    attrs = {k.lower(): v for k, _, v in (p.partition("=") for p in parts[1:])}
    expires: float | None = None
    if "max-age" in attrs:
        try:
            age = int(attrs["max-age"])
            expires = time.time() + age
            if age <= 0:
                return name, None, expires
        except ValueError:
            pass
    elif "expires" in attrs:
        try:
            expires = email.utils.parsedate_to_datetime(attrs["expires"]).timestamp()
            if expires <= time.time():
                return name, None, expires
        except (TypeError, ValueError):
            pass
    if value == "":
        return name, None, expires
    return name, value, expires


def parse_cookie_header(text: str) -> dict[str, str]:
    """Accept a browser ``cookie:`` header ("a=b; c=d"), optionally prefixed with "cookie:"."""
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[7:]
    jar: dict[str, str] = {}
    for part in text.replace("\n", " ").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            jar[name.strip()] = value.strip()
    return jar


def iso(ts: float | None) -> str | None:
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


class Mfp:
    def __init__(self, store: Store, transport: httpx.AsyncBaseTransport | None = None):
        self.store = store
        self._http = httpx.AsyncClient(transport=transport, timeout=30, follow_redirects=False, headers={"User-Agent": UA})
        self._cookies: dict[str, str] | None = None
        self._token: str | None = None
        self._token_exp = 0.0
        self._user_id: str | None = None
        self._lock = asyncio.Lock()

    # ---- cookies ------------------------------------------------------
    @property
    def cookies(self) -> dict[str, str]:
        if self._cookies is None:
            self._cookies = dict(self.store.secret_get(COOKIE_SECRET) or {})
        return self._cookies

    def set_cookies(self, jar: dict[str, str]) -> None:
        self._cookies = dict(jar)
        self._token, self._token_exp = None, 0.0
        self.store.secret_set(COOKIE_SECRET, self._cookies)

    @property
    def connected(self) -> bool:
        return bool(self.cookies)

    def _absorb(self, resp: httpx.Response) -> None:
        changed = False
        for header in resp.headers.get_list("set-cookie"):
            name, value, expires = parse_set_cookie(header)
            if value is None:
                changed |= self.cookies.pop(name, None) is not None
                continue
            if self.cookies.get(name) != value:
                self.cookies[name] = value
                changed = True
            if name == "last_login_date" and expires:
                self.store.meta_set("session_expires", iso(expires))
        if changed:
            self.store.secret_set(COOKIE_SECRET, self.cookies)

    async def _web(self, method: str, path: str, **kw: Any) -> httpx.Response:
        if not self.connected:
            raise SessionLost("not connected")
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()), **kw.pop("headers", {})}
        resp = await self._http.request(method, WEB + path, headers=headers, **kw)
        self._absorb(resp)
        if resp.status_code in (301, 302, 303, 307, 308) and "/account/login" in resp.headers.get("location", ""):
            raise SessionLost("redirected to login")
        return resp

    # ---- token / keep-alive ------------------------------------------
    async def refresh(self) -> dict[str, Any]:
        """Fetch a bearer token and renew the web session. This is also the keep-alive call."""
        async with self._lock:
            resp = await self._web("GET", "/user/auth_token?refresh=true", headers={"Accept": "application/json"})
            if resp.status_code != 200 or "json" not in resp.headers.get("content-type", ""):
                raise SessionLost(f"auth_token returned {resp.status_code}")
            data = resp.json()
            self._token = data["access_token"]
            self._user_id = str(data["user_id"])
            self._token_exp = time.time() + int(data.get("expires_in", 3600)) - 120
            self.store.meta_set("last_keepalive", iso(time.time()))
            self.store.meta_set("last_error", None)
            return data

    async def _ensure_token(self) -> None:
        if not self._token or time.time() >= self._token_exp:
            await self.refresh()

    async def api(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
        await self._ensure_token()
        for attempt in (1, 2):
            headers = {
                "Authorization": f"Bearer {self._token}",
                "mfp-client-id": "mfp-main-js",
                "mfp-user-id": self._user_id or "",
                "Accept": "application/json",
            }
            resp = await self._http.request(method, API + path, params=params, json=json, headers=headers)
            if resp.status_code == 401 and attempt == 1:
                await self.refresh()
                continue
            break
        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = body.get("error_description") or body.get("error") or resp.text[:200]
                if body.get("error_details"):
                    msg += f" {body['error_details']}"
            except ValueError:
                msg = resp.text[:200]
            raise MfpError(resp.status_code, msg)
        return resp.json() if resp.content else None

    async def username(self) -> str | None:
        cached = self.store.meta_get("username")
        if cached:
            return cached
        await self._ensure_token()
        item = (await self.api("GET", f"/v2/users/{self._user_id}")).get("item", {})
        if item.get("username"):
            self.store.meta_set("username", item["username"])
        return item.get("username")

    # ---- diary --------------------------------------------------------
    async def entries(self, date: str) -> list[dict]:
        return (await self.api("GET", "/v2/diary", params={"entry_date": date, "types": "food_entry"}))["items"]

    async def goals(self, date: str) -> dict[str, Any] | None:
        items = (await self.api("GET", "/v2/nutrient-goals", params={"date": date}))["items"]
        if not items:
            return None
        item = items[0]
        weekday = dt.date.fromisoformat(date).strftime("%A").lower()
        for g in item.get("daily_goals", []):
            if g.get("day_of_week") == weekday:
                return g
        return item.get("default_goal")

    async def food(self, food_id: str) -> dict[str, Any]:
        return (await self.api("GET", f"/v2/foods/{food_id}"))["item"]

    async def add_entry(self, date: str, meal: str, food: dict, serving_index: int, servings: float) -> dict:
        meal_name, position = meal_info(meal)
        sizes = food["serving_sizes"]
        if not 0 <= serving_index < len(sizes):
            raise MfpError(400, f"serving index {serving_index} out of range (food has {len(sizes)} serving sizes)")
        s = sizes[serving_index]
        body = {
            "items": [
                {
                    "type": "food_entry",
                    "date": date,
                    "meal_name": meal_name,
                    "meal_position": position,
                    "food": {"id": food["id"], "version": food["version"]},
                    "serving_size": {"value": s["value"], "unit": s["unit"], "nutrition_multiplier": s["nutrition_multiplier"]},
                    "servings": servings,
                }
            ]
        }
        before = {e["id"] for e in await self.entries(date)}
        await self.api("POST", "/v2/diary", json=body)
        new = [e for e in await self.entries(date) if e["id"] not in before]
        return new[0] if new else {}

    async def edit_entry(self, entry_id: str, servings: float) -> None:
        await self.api("PATCH", f"/v2/diary/{entry_id}", json={"item": {"servings": servings}})

    async def delete_entry(self, entry_id: str) -> None:
        await self.api("DELETE", f"/v2/diary/{entry_id}")

    # ---- custom foods ---------------------------------------------------
    async def create_food(self, name: str, serving: str, kcal: float, protein: float, carbs: float, fat: float) -> dict:
        item = {
            "description": name,
            "brand_name": "",
            "public": False,
            "serving_sizes": [{"value": 1.0, "unit": serving, "nutrition_multiplier": 1.0}],
            "nutritional_contents": {"energy": {"unit": "calories", "value": kcal}, "fat": fat, "carbohydrates": carbs, "protein": protein},
        }
        resp = await self.api("POST", "/v2/foods", json={"item": item})
        created = resp.get("item") or (resp.get("items") or [{}])[0]
        return await self.food(created["id"])

    # ---- search (web scrape) -------------------------------------------
    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        page = await self._web("GET", "/food/search")
        doc = lxml.html.fromstring(page.text)
        tokens = doc.xpath("(//input[@name='authenticity_token']/@value)[1]")
        if not tokens:
            raise SessionLost("search page had no form (not logged in?)")
        resp = await self._web(
            "POST",
            "/food/search",
            data={"authenticity_token": tokens[0], "search": query, "date": dt.date.today().isoformat(), "meal": "0"},
        )
        return parse_search_results(resp.text)[:limit]


def meal_info(meal: str) -> tuple[str, int]:
    try:
        return MEALS[meal.strip().casefold()]
    except KeyError:
        raise MfpError(400, f"unknown meal '{meal}' (use breakfast, lunch, dinner or snacks)") from None


def parse_search_results(markup: str) -> list[dict[str, Any]]:
    doc = lxml.html.fromstring(markup)
    out = []
    for li in doc.xpath("//li[contains(@class,'matched-food')]"):
        a = li.xpath(".//div[contains(@class,'search-title-container')]/a")
        if not a or not a[0].get("data-external-id"):
            continue
        info = li.xpath(".//p[contains(@class,'search-nutritional-info')]")
        brand = serving = ""
        kcal = None
        if info:
            parts = [p.strip() for p in (info[0].text or "").split(",")]
            try:
                kcal = float(parts[-1].replace("calories", "").strip())
            except ValueError:
                kcal = None
            if len(parts) >= 2:
                serving = parts[-2]
            if len(parts) >= 3:
                brand = ", ".join(parts[:-2])
        out.append(
            {
                "food_id": a[0].get("data-external-id"),
                "name": (a[0].text or "").strip(),
                "brand": brand,
                "serving": serving,
                "kcal": kcal,
                "verified": bool(li.xpath(".//div[contains(@class,'verified')]")),
            }
        )
    return out
