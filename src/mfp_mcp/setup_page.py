"""/setup: paste the browser's cookie header once; the server validates and stores it encrypted."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from .auth import passcode_ok
from .config import Config
from .mfp import Mfp, SessionLost, parse_cookie_header
from .ui import RateLimiter, esc, page

HELP = """
<ol>
<li>Open <b>myfitnesspal.com</b> in a desktop browser and log in.</li>
<li>Open DevTools (F12) → <b>Network</b>, reload, and click the first <code>www.myfitnesspal.com</code> request.</li>
<li>Under <b>Request Headers</b> find <code>cookie:</code> and use <b>Copy value</b>.</li>
<li>Paste it below with your passcode. Nothing is shown again after you submit.</li>
</ol>
"""


def form(error: str = "", ok: str = "") -> str:
    msg = (f"<p class=err>{esc(error)}</p>" if error else "") + (f"<p class=ok>{ok}</p>" if ok else "")
    return (
        f"{msg}{HELP}<form method=post action=/setup>"
        f"<label>Passcode<input type=password name=passcode autocomplete=current-password required></label>"
        f"<label>Cookie header<textarea name=cookies required spellcheck=false autocomplete=off></textarea></label>"
        f"<button>Connect to MyFitnessPal</button></form>"
    )


def make_handlers(config: Config, mfp: Mfp, limiter: RateLimiter):
    async def get(request: Request) -> Response:
        return page("MyFitnessPal setup", form())

    async def post(request: Request) -> Response:
        data = await request.form()
        key = limiter.client_key(request)
        if limiter.blocked(key):
            return page("Too many attempts", "<p class=err>Too many wrong passcodes. Try again in 10 minutes.</p>", 429)
        if not passcode_ok(str(data.get("passcode", "")), config.passcode):
            limiter.fail(key)
            return page("MyFitnessPal setup", form("Wrong passcode."), 401)
        limiter.reset(key)
        jar = parse_cookie_header(str(data.get("cookies", "")))
        if len(jar) < 5:
            return page("MyFitnessPal setup", form("That doesn't look like a full cookie header (expected ~25 cookies)."), 400)
        previous = dict(mfp.cookies)
        mfp.set_cookies(jar)
        try:
            await mfp.refresh()
            mfp.store.meta_set("username", None)
            name = await mfp.username()
        except SessionLost:
            if previous:
                mfp.set_cookies(previous)
            else:
                mfp.store.secret_delete("mfp_cookies")
                mfp._cookies = {}
            return page(
                "MyFitnessPal setup",
                form("MyFitnessPal did not accept those cookies. Log in again in the browser and copy a fresh header."),
                400,
            )
        until = mfp.store.meta_get("session_expires") or "unknown"
        return page(
            "Connected",
            f"<p class=ok>Connected as <b>{esc(name or '?')}</b>. Session valid until {esc(until)} and renewed automatically.</p>"
            "<p>You can close this page and add the connector in Claude.</p>",
        )

    return get, post
