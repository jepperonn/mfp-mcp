"""The MCP server: tools, OAuth, /login, /setup."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import functools
import logging
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, BaseModel, Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import keepalive
from .auth import SCOPES, MfpAuthProvider
from .config import Config
from .mfp import Mfp, MfpError, SessionLost, meal_info
from .setup_page import make_handlers
from .store import Store, norm
from .ui import RateLimiter

log = logging.getLogger("mfp_mcp")


class LogItem(BaseModel):
    food: str = Field(description="A My Foods name or alias, or a numeric MyFitnessPal food id from search_food")
    servings: float = Field(1.0, description="Number of servings", gt=0)
    serving: int = Field(0, description="Serving-size index for MFP foods (see food_info); My Foods always use 0")


def r1(x: float | None) -> float:
    return round(float(x or 0), 1)


def macros(nc: dict[str, Any]) -> dict[str, float]:
    return {
        "kcal": r1((nc.get("energy") or {}).get("value")),
        "protein": r1(nc.get("protein")),
        "carbs": r1(nc.get("carbohydrates")),
        "fat": r1(nc.get("fat")),
    }


def entry_summary(e: dict[str, Any]) -> dict[str, Any]:
    food, ss = e.get("food", {}), e.get("serving_size", {})
    name = food.get("description", "?")
    if food.get("brand_name"):
        name += f" ({food['brand_name']})"
    return {
        "id": e["id"],
        "name": name,
        "servings": e.get("servings"),
        "serving": f"{ss.get('value')} {ss.get('unit')}",
        **macros(e.get("nutritional_contents", {})),
    }


def create_app(
    config: Config, store: Store | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[Starlette, Mfp, Store]:
    store = store or Store(config.db_path, config.secret_key)
    mfp = Mfp(store, transport=transport)
    limiter = RateLimiter()
    provider = MfpAuthProvider(store, config, limiter)
    tz = ZoneInfo(config.timezone)
    setup_url = f"{config.public_url}/setup"

    def today() -> str:
        return dt.datetime.now(tz).date().isoformat()

    def guarded(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            try:
                return await fn(*a, **kw)
            except SessionLost:
                raise ToolError(f"MyFitnessPal login lost – open {setup_url} and paste fresh cookies") from None
            except MfpError as exc:
                raise ToolError(str(exc)) from None

        return wrapper

    def valid_date(date: str | None) -> str:
        if not date:
            return today()
        try:
            return dt.date.fromisoformat(date).isoformat()
        except ValueError:
            raise ToolError(f"date must be YYYY-MM-DD, got '{date}'") from None

    server = MCPServer(
        "MyFitnessPal",
        instructions=(
            "Log and review food in the user's MyFitnessPal diary. Prefer My Foods (saved standard foods) when the user "
            "names one. For other foods use search_food, show the draft (food, amount, kcal, meal) and log it only after the user agrees."
        ),
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(config.public_url),
            resource_server_url=AnyHttpUrl(f"{config.public_url}/mcp"),
            validate_token_resource=False,
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )

    # ---- helpers -------------------------------------------------------
    def find_my_food(ref: str) -> dict[str, Any] | None:
        key = norm(ref)
        for f in store.food_rows():
            if key == norm(f["name"]) or key in [norm(a) for a in f["aliases"]]:
                return f
        return None

    async def day_summary(date: str) -> dict[str, Any]:
        entries = await mfp.entries(date)
        goals = await mfp.goals(date)
        meals: dict[str, list] = {}
        totals = {"kcal": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}
        for e in sorted(entries, key=lambda e: (e.get("meal_position", 9), e.get("created_at", ""))):
            s = entry_summary(e)
            meals.setdefault(e.get("meal_name", "?"), []).append(s)
            for k in totals:
                totals[k] += s[k]
        totals = {k: r1(v) for k, v in totals.items()}
        out: dict[str, Any] = {"date": date, "meals": meals, "totals": totals}
        if goals:
            g = macros(goals)
            out["goals"] = g
            out["remaining"] = {k: r1(g[k] - totals[k]) for k in totals}
        return out

    # ---- tools ---------------------------------------------------------
    @server.tool()
    @guarded
    async def get_day(date: str | None = None) -> dict:
        """Food diary for a day (default today): entries per meal with ids, totals, MyFitnessPal goals and what remains."""
        return await day_summary(valid_date(date))

    @server.tool()
    @guarded
    async def search_food(query: str) -> dict:
        """Search for a food. My Foods (saved standard foods) come first, then MyFitnessPal's database."""
        q = norm(query)
        mine = [
            {
                "source": "my_foods",
                "name": f["name"],
                "aliases": f["aliases"],
                "serving": f["serving"],
                "kcal": f["kcal"],
                "protein": f["protein"],
                "carbs": f["carbs"],
                "fat": f["fat"],
            }
            for f in store.food_rows()
            if q in norm(f["name"]) or any(q in norm(a) for a in f["aliases"])
        ]
        return {"my_foods": mine, "mfp": await mfp.search(query)}

    @server.tool()
    @guarded
    async def food_info(food_id: str) -> dict:
        """Serving sizes and nutrition of a MyFitnessPal food id (from search_food). Use the serving index with log_food."""
        f = await mfp.food(food_id)
        nc = f.get("nutritional_contents", {})
        return {
            "food_id": f["id"],
            "name": f.get("description"),
            "brand": f.get("brand_name"),
            "per_serving_size_0": macros(nc),
            "serving_sizes": [
                {"index": i, "unit": s["unit"], "multiplier": s["nutrition_multiplier"]} for i, s in enumerate(f["serving_sizes"])
            ],
        }

    @server.tool()
    @guarded
    async def log_food(meal: str, items: list[LogItem], date: str | None = None) -> dict:
        """Log one or more foods to a meal (breakfast, lunch, dinner, snacks). Nothing is logged if any item cannot be resolved."""
        d = valid_date(date)
        meal_info(meal)
        plan = []
        for it in items:
            row = find_my_food(it.food)
            if row:
                if not row["mfp_food_id"]:
                    raise ToolError(f"'{row['name']}' has no MyFitnessPal food; save it again with my_foods_save")
                food = {
                    "id": row["mfp_food_id"],
                    "version": row["mfp_food_version"],
                    "serving_sizes": [{"value": 1.0, "unit": row["serving"], "nutrition_multiplier": 1.0}],
                }
                plan.append((food, 0, it.servings))
            elif it.food.strip().isdigit():
                plan.append((await mfp.food(it.food.strip()), it.serving, it.servings))
            else:
                raise ToolError(f"'{it.food}' is not in My Foods; use search_food to find a MyFitnessPal food id")
        logged = [entry_summary(await mfp.add_entry(d, meal, food, idx, n)) for food, idx, n in plan]
        day = await day_summary(d)
        return {"logged": logged, "day_totals": day["totals"], "remaining": day.get("remaining")}

    @server.tool()
    @guarded
    async def edit_entry(entry_id: str, servings: float) -> dict:
        """Change the number of servings of a diary entry (ids come from get_day)."""
        if servings <= 0:
            raise ToolError("servings must be greater than 0")
        await mfp.edit_entry(entry_id, servings)
        return {"edited": entry_id, "servings": servings}

    @server.tool()
    @guarded
    async def delete_entry(entry_id: str) -> dict:
        """Delete a diary entry (ids come from get_day)."""
        await mfp.delete_entry(entry_id)
        return {"deleted": entry_id}

    @server.tool()
    def my_foods_list() -> list[dict]:
        """The saved standard foods ('My Foods') that log_food can log in one step."""
        return [{k: f[k] for k in ("name", "aliases", "serving", "kcal", "protein", "carbs", "fat")} for f in store.food_rows()]

    @server.tool()
    @guarded
    async def my_foods_save(
        name: str, kcal: float, protein: float, carbs: float, fat: float, serving: str = "1 serving", aliases: list[str] | None = None
    ) -> dict:
        """Save (or update) a standard food with exact numbers per serving. It is also created as a custom food in MyFitnessPal."""
        if min(kcal, protein, carbs, fat) < 0:
            raise ToolError("nutrition values cannot be negative")
        created = await mfp.create_food(name, serving, kcal, protein, carbs, fat)
        store.food_put(
            {
                "name": name,
                "aliases": aliases or [],
                "serving": serving,
                "kcal": kcal,
                "protein": protein,
                "carbs": carbs,
                "fat": fat,
                "mfp_food_id": created["id"],
                "mfp_food_version": created["version"],
            }
        )
        return {"saved": name, "aliases": aliases or [], "serving": serving, "kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat}

    @server.tool()
    def my_foods_delete(name: str) -> dict:
        """Remove a food from My Foods. Past diary entries and the MyFitnessPal custom food are left untouched."""
        row = find_my_food(name)
        if not row:
            raise ToolError(f"'{name}' is not in My Foods")
        store.food_delete(norm(row["name"]))
        return {"deleted": row["name"]}

    @server.tool()
    async def status() -> dict:
        """Is the MyFitnessPal connection healthy? Shows account, session expiry and the last keep-alive."""
        out: dict[str, Any] = {"connected": mfp.connected, "setup_url": setup_url}
        if not mfp.connected:
            out["problem"] = f"Not connected – open {setup_url} and paste cookies"
            return out
        out.update(
            session_valid_until=store.meta_get("session_expires"),
            last_keepalive=store.meta_get("last_keepalive"),
            last_error=store.meta_get("last_error"),
            my_foods=len(store.food_rows()),
        )
        try:
            await mfp.refresh()
            out["username"] = await mfp.username()
            out["login_ok"] = True
            out["session_valid_until"] = store.meta_get("session_expires")
            until = out["session_valid_until"]
            if until and dt.datetime.fromisoformat(until.replace("Z", "+00:00")) - dt.datetime.now(dt.UTC) < dt.timedelta(days=7):
                out["warning"] = f"Session expires within 7 days – open {setup_url} to paste fresh cookies"
        except SessionLost:
            out.update(login_ok=False, problem=f"MyFitnessPal login lost – open {setup_url} and paste fresh cookies")
        except Exception as exc:
            out.update(login_ok=None, problem=f"Could not reach MyFitnessPal: {type(exc).__name__}")
        return out

    # ---- web routes ----------------------------------------------------
    @server.custom_route("/login", methods=["GET"])
    async def login_get(request: Request):
        return await provider.login_page(request)

    @server.custom_route("/login", methods=["POST"])
    async def login_post(request: Request):
        return await provider.login_submit(request)

    setup_get, setup_post = make_handlers(config, mfp, limiter)
    server.custom_route("/setup", methods=["GET"])(setup_get)
    server.custom_route("/setup", methods=["POST"])(setup_post)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request):
        return JSONResponse({"ok": True, "connected": mfp.connected})

    hosts = [config.host]
    if config.host.split(":")[0] in ("localhost", "127.0.0.1"):
        hosts += ["localhost:*", "127.0.0.1:*"]
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        host=config.host.split(":")[0],
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=[config.public_url, "https://claude.ai"],
        ),
    )

    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a):
        task = asyncio.create_task(keepalive.run_forever(mfp, store))
        try:
            async with inner(a):
                yield
        finally:
            task.cancel()
            await mfp._http.aclose()

    app.router.lifespan_context = lifespan
    return app, mfp, store


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    config = Config.from_env()
    app, _, _ = create_app(config)
    uvicorn.run(app, host="0.0.0.0", port=int(__import__("os").environ.get("PORT", "8000")), proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
