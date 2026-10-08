"""Tiny HTML helpers shared by the login and setup pages."""

from __future__ import annotations

import html
import time

from starlette.requests import Request
from starlette.responses import HTMLResponse

CSS = """
body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:3rem auto;padding:0 1rem;color:#1a1a1a}
input,textarea,button{font:inherit;width:100%;box-sizing:border-box;padding:.6rem;margin:.3rem 0 1rem}
textarea{min-height:9rem;font-family:ui-monospace,monospace;font-size:.8rem}
button{background:#0b5fff;color:#fff;border:0;border-radius:6px;cursor:pointer}
.ok{background:#e7f6ec;padding:.8rem;border-radius:6px}.err{background:#fdecec;padding:.8rem;border-radius:6px}
code{background:#f1f1f1;padding:0 .25rem;border-radius:3px}
@media(prefers-color-scheme:dark){body{background:#151515;color:#eee}code{background:#2a2a2a}
.ok{background:#17301f}.err{background:#3a1c1c}}
"""

esc = html.escape


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = (
        f"<!doctype html><html lang=en><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{CSS}</style><h1>{esc(title)}</h1>{body}</html>"
    )
    return HTMLResponse(doc, status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


class RateLimiter:
    """Blocks a client after too many wrong passcodes (in memory, per process)."""

    def __init__(self, max_failures: int = 5, window: float = 600):
        self.max, self.window = max_failures, window
        self._fails: dict[str, list[float]] = {}

    @staticmethod
    def client_key(request: Request) -> str:
        return request.headers.get("fly-client-ip") or (request.client.host if request.client else "?")

    def _recent(self, key: str) -> list[float]:
        now = time.time()
        recent = [t for t in self._fails.get(key, []) if now - t < self.window]
        self._fails[key] = recent
        return recent

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max

    def fail(self, key: str) -> None:
        self._recent(key).append(time.time())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)
