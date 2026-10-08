"""Background task that renews the MyFitnessPal session so it never expires."""

from __future__ import annotations

import asyncio
import logging

from .mfp import Mfp, SessionLost
from .store import Store

log = logging.getLogger("mfp_mcp.keepalive")
INTERVAL = 6 * 3600
FIRST_DELAY = 20


async def run_once(mfp: Mfp, store: Store) -> bool:
    if not mfp.connected:
        return False
    try:
        await mfp.refresh()
        return True
    except SessionLost:
        store.meta_set("last_error", "login lost")
        log.warning("MyFitnessPal login lost; waiting for new cookies on /setup")
    except Exception as exc:  # network problems etc.: keep trying next round
        store.meta_set("last_error", f"keep-alive failed: {type(exc).__name__}")
        log.warning("keep-alive failed: %s", exc)
    return False


async def run_forever(mfp: Mfp, store: Store, interval: float = INTERVAL, first_delay: float = FIRST_DELAY) -> None:
    await asyncio.sleep(first_delay)
    while True:
        await run_once(mfp, store)
        await asyncio.sleep(interval)
