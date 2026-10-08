"""OAuth authorization server for Claude's custom connector.

Everything (clients, codes, tokens) lives in SQLite, so logins survive restarts and deploys.
The only "user" is the owner, who proves it by typing the passcode on /login.
"""

from __future__ import annotations

import hmac
import json
import secrets
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .config import Config
from .store import Store
from .ui import RateLimiter, esc, page

ACCESS_TTL = 3600  # 1 hour
REFRESH_TTL = 180 * 24 * 3600  # 180 days, rotated on every use
CODE_TTL = 300
PENDING_TTL = 600
REFRESH_GRACE = 120  # old refresh token still works briefly after rotation (lost responses)
SCOPES = ["mfp"]


def passcode_ok(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


class MfpAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, store: Store, config: Config, limiter: RateLimiter):
        self.store, self.config, self.limiter = store, config, limiter

    # ---- client registration ----------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self.store.client_get(client_id)
        return OAuthClientInformationFull.model_validate_json(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.store.client_put(client_info.client_id, client_info.model_dump_json())

    # ---- authorization ----------------------------------------------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        pid = secrets.token_urlsafe(24)
        self.store.pending_put(
            pid,
            json.dumps(
                {
                    "client_id": client.client_id,
                    "state": params.state,
                    "scopes": params.scopes or SCOPES,
                    "code_challenge": params.code_challenge,
                    "redirect_uri": str(params.redirect_uri),
                    "explicit": params.redirect_uri_provided_explicitly,
                    "resource": params.resource,
                }
            ),
            PENDING_TTL,
        )
        return f"{self.config.public_url}/login?req={pid}"

    async def login_page(self, request: Request) -> Response:
        pid = request.query_params.get("req", "")
        if not self.store.pending_get(pid):
            return page("Login expired", "<p>This login link has expired. Go back to Claude and try connecting again.</p>", 400)
        return page("Connect Claude", self._form(pid))

    def _form(self, pid: str, error: str = "") -> str:
        err = f"<p class=err>{esc(error)}</p>" if error else ""
        return (
            f"{err}<p>Enter the passcode for your MyFitnessPal server.</p>"
            f"<form method=post action=/login><input type=hidden name=req value='{esc(pid)}'>"
            f"<input type=password name=passcode autocomplete=current-password autofocus required>"
            f"<button>Connect</button></form>"
        )

    async def login_submit(self, request: Request) -> Response:
        form = await request.form()
        pid, given = str(form.get("req", "")), str(form.get("passcode", ""))
        raw = self.store.pending_get(pid)
        if not raw:
            return page("Login expired", "<p>This login link has expired. Go back to Claude and try connecting again.</p>", 400)
        key = self.limiter.client_key(request)
        if self.limiter.blocked(key):
            return page("Too many attempts", "<p class=err>Too many wrong passcodes. Try again in 10 minutes.</p>", 429)
        if not passcode_ok(given, self.config.passcode):
            self.limiter.fail(key)
            return page("Connect Claude", self._form(pid, "Wrong passcode."), 401)
        self.limiter.reset(key)
        p = json.loads(raw)
        self.store.pending_delete(pid)
        code = secrets.token_urlsafe(32)
        self.store.code_put(
            code,
            AuthorizationCode(
                code=code,
                scopes=p["scopes"],
                expires_at=time.time() + CODE_TTL,
                client_id=p["client_id"],
                code_challenge=p["code_challenge"],
                redirect_uri=p["redirect_uri"],
                redirect_uri_provided_explicitly=p["explicit"],
                resource=p["resource"],
                subject="owner",
            ).model_dump_json(),
            CODE_TTL,
        )
        return RedirectResponse(construct_redirect_uri(p["redirect_uri"], code=code, state=p["state"]), status_code=302)

    # ---- code / token exchange --------------------------------------
    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        data = self.store.code_get(authorization_code)
        if not data:
            return None
        code = AuthorizationCode.model_validate_json(data)
        return code if code.client_id == client.client_id else None

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        self.store.code_delete(authorization_code.code)  # single use
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource, authorization_code.subject)

    def _issue(self, client_id: str, scopes: list[str], resource: str | None, subject: str | None) -> OAuthToken:
        family = secrets.token_urlsafe(12)
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        a = AccessToken(token="", client_id=client_id, scopes=scopes, expires_at=now + ACCESS_TTL, resource=resource, subject=subject)
        r = RefreshToken(token="", client_id=client_id, scopes=scopes, expires_at=now + REFRESH_TTL, resource=resource, subject=subject)
        self.store.token_put(access, "access", client_id, a.model_dump_json(), a.expires_at, family)
        self.store.token_put(refresh, "refresh", client_id, r.model_dump_json(), r.expires_at, family)
        self.store.token_purge_expired()
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL, scope=" ".join(scopes), refresh_token=refresh)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        row = self.store.token_get(refresh_token, "refresh")
        if not row or row["client_id"] != client.client_id:
            return None
        tok = RefreshToken.model_validate_json(row["data"])
        return tok.model_copy(update={"token": refresh_token})

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        # Rotate, but keep the old refresh token alive for a short grace period in case the response gets lost.
        row = self.store.token_get(refresh_token.token, "refresh")
        if row:
            grace = min(row["expires_at"] or float("inf"), time.time() + REFRESH_GRACE)
            self.store.token_put(refresh_token.token, "refresh", row["client_id"], row["data"], grace, row["family"])
        granted = [s for s in scopes if s in refresh_token.scopes] or refresh_token.scopes
        return self._issue(client.client_id, granted, refresh_token.resource, refresh_token.subject)

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self.store.token_get(token, "access")
        if not row:
            return None
        return AccessToken.model_validate_json(row["data"]).model_copy(update={"token": token})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        for kind in ("access", "refresh"):
            row = self.store.token_get(token.token, kind)
            if row:
                self.store.token_delete_family(row["family"])
