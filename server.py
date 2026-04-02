"""OAuth authorization server provider for the QuickBooks MCP
server.

Implements the MCP SDK's OAuthAuthorizationServerProvider protocol,
delegating user authentication to an external auth worker (Google
OAuth) via the double-OAuth pattern. The MCP SDK handles client
registration, token exchange, bearer auth middleware, and well-known
metadata automatically.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

# ---------------------------------------------------------------------------
# Pending authorization context
# ---------------------------------------------------------------------------

_PENDING_TTL = 600  # 10 minutes
_AUTH_CODE_TTL = 300  # 5 minutes
_ACCESS_TOKEN_TTL = 28800  # 8 hours
_REFRESH_TOKEN_TTL = 86400 * 30  # 30 days


@dataclass
class PendingAuthContext:
    """Tracks an in-flight authorization so we can map the auth
    worker callback back to the original Claude.ai request."""

    client_id: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    state: str | None
    code_challenge: str
    scopes: list[str]
    our_pkce_verifier: str
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# OAuth authorization server provider
# ---------------------------------------------------------------------------


class NthParallelAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Implements MCP SDK OAuth provider, delegating user
    authentication to an external auth worker."""

    def __init__(
        self,
        auth_worker_base_url: str,
        worker_client_id: str,
        server_base_url: str,
    ) -> None:
        self._auth_worker_base_url = auth_worker_base_url.rstrip("/")
        self._worker_client_id = worker_client_id
        self._server_base_url = server_base_url.rstrip("/")

        # In-memory stores
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        self._pending_auths: dict[str, PendingAuthContext] = {}

    # -- Client registration -----------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info

    # -- Authorization -----------------------------------------------

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        # Generate PKCE for OUR call to the auth worker
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )

        # State token that maps the auth worker callback
        # back to Claude.ai's pending authorization
        our_state = secrets.token_urlsafe(32)

        self._pending_auths[our_state] = PendingAuthContext(
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=(params.redirect_uri_provided_explicitly),
            state=params.state,
            code_challenge=params.code_challenge,
            scopes=params.scopes or [],
            our_pkce_verifier=verifier,
        )

        # Build redirect URL to auth worker
        query = urlencode(
            {
                "client_id": self._worker_client_id,
                "redirect_uri": (f"{self._server_base_url}/oauth/callback"),
                "response_type": "code",
                "state": our_state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self._auth_worker_base_url}/oauth/authorize?{query}"

    # -- Authorization code ------------------------------------------

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        code = self._auth_codes.get(authorization_code)
        if code is None:
            return None
        if code.client_id != client.client_id:
            return None
        if time.time() > code.expires_at:
            self._auth_codes.pop(authorization_code, None)
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        # Consume the code
        self._auth_codes.pop(authorization_code.code, None)

        # Issue opaque access + refresh tokens
        access_tok = secrets.token_urlsafe(32)
        refresh_tok = secrets.token_urlsafe(32)
        scopes = authorization_code.scopes

        self._access_tokens[access_tok] = AccessToken(
            token=access_tok,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=int(time.time()) + _ACCESS_TOKEN_TTL,
        )
        self._refresh_tokens[refresh_tok] = RefreshToken(
            token=refresh_tok,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=int(time.time()) + _REFRESH_TOKEN_TTL,
        )

        return OAuthToken(
            access_token=access_tok,
            token_type="Bearer",
            expires_in=_ACCESS_TOKEN_TTL,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=refresh_tok,
        )

    # -- Access tokens -----------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        at = self._access_tokens.get(token)
        if at is None:
            return None
        if at.expires_at and time.time() > at.expires_at:
            self._access_tokens.pop(token, None)
            return None
        return at

    # -- Refresh tokens ----------------------------------------------

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        rt = self._refresh_tokens.get(refresh_token)
        if rt is None:
            return None
        if rt.client_id != client.client_id:
            return None
        if rt.expires_at and time.time() > rt.expires_at:
            self._refresh_tokens.pop(refresh_token, None)
            return None
        return rt

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Remove old tokens
        self._refresh_tokens.pop(refresh_token.token, None)

        # Issue new pair
        access_tok = secrets.token_urlsafe(32)
        new_refresh_tok = secrets.token_urlsafe(32)

        self._access_tokens[access_tok] = AccessToken(
            token=access_tok,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=int(time.time()) + _ACCESS_TOKEN_TTL,
        )
        self._refresh_tokens[new_refresh_tok] = RefreshToken(
            token=new_refresh_tok,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=int(time.time()) + _REFRESH_TOKEN_TTL,
        )

        return OAuthToken(
            access_token=access_tok,
            token_type="Bearer",
            expires_in=_ACCESS_TOKEN_TTL,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=new_refresh_tok,
        )

    # -- Revocation --------------------------------------------------

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
    ) -> None:
        if isinstance(token, AccessToken):
            self._access_tokens.pop(token.token, None)
        elif isinstance(token, RefreshToken):
            self._refresh_tokens.pop(token.token, None)

    # -- Auth worker callback ----------------------------------------

    async def handle_callback(self, request: Request) -> Response:
        """Handle the redirect from the auth worker after Google
        authentication completes."""
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        error = request.query_params.get("error")

        if not state or state not in self._pending_auths:
            return JSONResponse(
                {"error": "invalid_state"},
                status_code=400,
            )

        pending = self._pending_auths.pop(state)

        # Check if auth worker returned an error
        if error or not code:
            return RedirectResponse(
                url=construct_redirect_uri(
                    pending.redirect_uri,
                    error=error or "server_error",
                    state=pending.state,
                ),
                status_code=302,
            )

        # Exchange auth worker's code for a JWT
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._auth_worker_base_url}/oauth/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": (f"{self._server_base_url}/oauth/callback"),
                        "code_verifier": pending.our_pkce_verifier,
                    },
                    headers={
                        "Content-Type": ("application/x-www-form-urlencoded"),
                    },
                )
                resp.raise_for_status()
                token_data = resp.json()
        except Exception as exc:
            print(
                f"Auth worker token exchange failed: {exc}",
                file=sys.stderr,
            )
            return RedirectResponse(
                url=construct_redirect_uri(
                    pending.redirect_uri,
                    error="server_error",
                    error_description="Token exchange failed",
                    state=pending.state,
                ),
                status_code=302,
            )

        # Log the authenticated user (decode JWT payload)
        jwt_token = token_data.get("access_token", "")
        try:
            import json

            payload_b64 = jwt_token.split(".")[1]
            # Add padding
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.b64decode(payload_b64))
            email = payload.get("email", "unknown")
            print(f"AUTH OK: {email}", file=sys.stderr)
        except Exception:
            print(
                "AUTH OK: (could not decode JWT)",
                file=sys.stderr,
            )

        # Create our own authorization code for Claude.ai
        our_code = secrets.token_urlsafe(32)
        self._auth_codes[our_code] = AuthorizationCode(
            code=our_code,
            scopes=pending.scopes,
            expires_at=time.time() + _AUTH_CODE_TTL,
            client_id=pending.client_id,
            code_challenge=pending.code_challenge,
            redirect_uri=AnyHttpUrl(pending.redirect_uri),
            redirect_uri_provided_explicitly=(pending.redirect_uri_provided_explicitly),
        )

        # Redirect to Claude.ai with our code
        return RedirectResponse(
            url=construct_redirect_uri(
                pending.redirect_uri,
                code=our_code,
                state=pending.state,
            ),
            status_code=302,
        )


# ---------------------------------------------------------------------------
# Configuration helper
# ---------------------------------------------------------------------------


def configure_auth(mcp: FastMCP) -> None:
    """Attach OAuth auth provider and custom routes to an
    existing FastMCP instance. Must be called before
    streamable_http_app()."""

    server_base_url = os.environ.get("SERVER_BASE_URL", "")
    auth_worker_base_url = os.environ.get(
        "AUTH_WORKER_BASE_URL", "https://auth.nthparallel.com"
    )
    worker_client_id = os.environ.get("AUTH_WORKER_CLIENT_ID", "")

    if not server_base_url:
        print(
            "WARNING: SERVER_BASE_URL is not set",
            file=sys.stderr,
        )
    if not worker_client_id:
        print(
            "WARNING: AUTH_WORKER_CLIENT_ID is not set",
            file=sys.stderr,
        )

    provider = NthParallelAuthProvider(
        auth_worker_base_url=auth_worker_base_url,
        worker_client_id=worker_client_id,
        server_base_url=server_base_url,
    )

    # Patch auth onto the existing FastMCP instance.
    # This is done after tool registration (which happens at
    # import time) but before streamable_http_app() is called.
    mcp._auth_server_provider = provider  # type: ignore[attr-defined]
    mcp.settings.auth = AuthSettings(
        issuer_url=AnyHttpUrl(server_base_url),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
        ),
    )

    # Register custom routes
    @mcp.custom_route("/oauth/callback", methods=["GET"])
    async def oauth_callback(request: Request) -> Response:
        return await provider.handle_callback(request)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})
