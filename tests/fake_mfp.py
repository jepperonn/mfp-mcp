"""A fake MyFitnessPal (web + API) for tests. No real data, no network."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs

import httpx

SEARCH_HTML = """<html><form><input name="authenticity_token" value="csrf123"></form>
<p>Matching Foods:</p><ul>
<li class="matched-food"><div class="search-title-container"><a data-external-id="555">Banana</a></div>
<div class="verified verified-list-icon"></div>
<p class="search-nutritional-info">Generic, medium, 105 calories</p></li>
<li class="matched-food"><div class="search-title-container"><a data-external-id="556">Banan (Rema)</a></div>
<p class="search-nutritional-info">Rema 1000, Rema, 100 g, 89 calories</p></li></ul></html>"""


class FakeMfp:
    def __init__(self):
        self.logged_in = True
        self.entries: dict[str, dict] = {}
        self.foods: dict[str, dict] = {
            "555": {
                "id": "555",
                "version": "v555",
                "description": "Banana",
                "brand_name": "",
                "serving_sizes": [
                    {"value": 1.0, "unit": "medium", "nutrition_multiplier": 1.0, "id": "s1"},
                    {"value": 100.0, "unit": "g", "nutrition_multiplier": 0.85, "id": "s2"},
                ],
                "nutritional_contents": {"energy": {"unit": "calories", "value": 105.0}, "protein": 1.3, "carbohydrates": 27.0, "fat": 0.4},
            }
        }
        self.cookies_seen: list[str] = []
        self.requests: list[tuple[str, str]] = []
        self._n = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _entry(self, item: dict) -> dict:
        self._n += 1
        food = self.foods[item["food"]["id"]]
        mult = item["serving_size"]["nutrition_multiplier"] * item["servings"]
        nc = food["nutritional_contents"]
        return {
            "id": f"e{self._n}",
            "type": "food_entry",
            "date": item["date"],
            "meal_name": item["meal_name"],
            "meal_position": item["meal_position"],
            "created_at": f"2026-01-01T00:00:{self._n:02d}Z",
            "food": food,
            "serving_size": item["serving_size"],
            "servings": item["servings"],
            "nutritional_contents": {
                "energy": {"unit": "calories", "value": nc["energy"]["value"] * mult},
                "protein": nc["protein"] * mult,
                "carbohydrates": nc["carbohydrates"] * mult,
                "fat": nc["fat"] * mult,
            },
        }

    def handle(self, req: httpx.Request) -> httpx.Response:
        path, method = req.url.path, req.method
        self.requests.append((method, path))
        if req.url.host == "www.myfitnesspal.com":
            self.cookies_seen.append(req.headers.get("cookie", ""))
            if not self.logged_in:
                return httpx.Response(302, headers={"location": "/account/login?callbackUrl=x"})
            if path == "/user/auth_token":
                return httpx.Response(
                    200,
                    json={"access_token": "tok", "user_id": "u1", "expires_in": 864000, "token_type": "Bearer"},
                    headers=[
                        ("set-cookie", "last_login_date=2026-11-07; Path=/; expires=Sat, 07 Nov 2099 17:31:55 GMT"),
                        ("set-cookie", "_mfp_session=renewed; Path=/"),
                        ("set-cookie", "__Secure-next-auth.session-token=; Path=/; Max-Age=0"),
                    ],
                )
            if path == "/food/search":
                return httpx.Response(200, text=SEARCH_HTML)
            return httpx.Response(404)
        # API host
        if req.headers.get("authorization") != "Bearer tok":
            return httpx.Response(401, json={"error": "unauthorized"})
        q = {k: v[0] for k, v in parse_qs(req.url.query.decode()).items()}
        body = json.loads(req.content) if req.content else None
        if path == "/v2/users/u1":
            return httpx.Response(200, json={"item": {"id": "u1", "username": "tester"}})
        if path == "/v2/nutrient-goals":
            g = {"energy": {"value": 2000.0, "unit": "calories"}, "protein": 150.0, "carbohydrates": 200.0, "fat": 70.0}
            return httpx.Response(200, json={"items": [{"daily_goals": [{"day_of_week": "monday", **g}], "default_goal": g}]})
        if path == "/v2/diary" and method == "GET":
            items = [e for e in self.entries.values() if e["date"] == q["entry_date"]]
            return httpx.Response(200, json={"items": items})
        if path == "/v2/diary" and method == "POST":
            for item in body["items"]:
                if set(item["serving_size"]) - {"value", "unit", "nutrition_multiplier"} or set(item["food"]) != {"id", "version"}:
                    return httpx.Response(400, json={"error": "failed-validation", "error_description": "Invalid Items"})
                e = self._entry(item)
                self.entries[e["id"]] = e
            return httpx.Response(201, json={"items": []})
        m = re.fullmatch(r"/v2/diary/(\w+)", path)
        if m and method == "PATCH":
            e = self.entries[m[1]]
            old = e["servings"]
            new = body["item"]["servings"]
            e["servings"] = new
            for k in ("protein", "carbohydrates", "fat"):
                e["nutritional_contents"][k] *= new / old
            e["nutritional_contents"]["energy"]["value"] *= new / old
            return httpx.Response(204)
        if m and method == "DELETE":
            self.entries.pop(m[1], None)
            return httpx.Response(204)
        if path == "/v2/foods" and method == "POST":
            it = body["item"]
            fid = str(900 + len(self.foods))
            self.foods[fid] = {
                "id": fid,
                "version": f"v{fid}",
                "description": it["description"],
                "brand_name": "",
                "serving_sizes": it["serving_sizes"],
                "nutritional_contents": it["nutritional_contents"],
            }
            return httpx.Response(200, json={"item": self.foods[fid]})
        m = re.fullmatch(r"/v2/foods/(\w+)", path)
        if m and method == "GET":
            return (
                httpx.Response(200, json={"item": self.foods[m[1]]})
                if m[1] in self.foods
                else httpx.Response(404, json={"error": "not found"})
            )
        return httpx.Response(404, json={"error": "nope"})
