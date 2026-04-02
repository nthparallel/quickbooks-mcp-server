"""JWT auth and well-known endpoints for the QuickBooks MCP server.

Claude.ai handles the full OAuth flow with the auth worker at
auth.nthparallel.com (including dynamic client registration).
The auth worker issues HS256 JWTs. This server verifies those
JWTs on /mcp requests and serves well-known discovery endpoints
so Claude.ai knows where to authenticate.

Instead of wrapping the MCP app in FastAPI, we add our routes
and middleware directly to the SDK's Starlette app to avoid
mount/redirect issues.
"""

import os
import sys

from jose import JWTError, jwt
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PUBLIC_PREFIXES = ("/.well-known/", "/health")


def _get_base_url(request: Request) -> str:
    """Return the public-facing base URL."""
    configured = os.getenv("SERVER_BASE_URL")
    if configured:
        return configured.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get(
        "x-forwarded-host",
        request.headers.get("host", request.url.netloc),
    )
    return f"{proto}://{host}"


# ---------------------------------------------------------------------------
# JWT authentication middleware
# ---------------------------------------------------------------------------


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Validates HS256 JWT Bearer tokens on /mcp requests."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path

        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        signing_secret = os.getenv("JWT_SIGNING_SECRET")
        issuer = os.getenv("JWT_ISSUER", "https://auth.nthparallel.com")
        base_url = _get_base_url(request)

        www_auth = f'Bearer realm="{issuer}", resource="{base_url}"'

        if not signing_secret:
            print(
                "AUTH REJECT: JWT_SIGNING_SECRET not set",
                file=sys.stderr,
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "error_description": ("Server JWT signing secret not configured"),
                },
                headers={"WWW-Authenticate": www_auth},
            )

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            print(
                f"AUTH REJECT: No Bearer token on {path}",
                file=sys.stderr,
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "error_description": ("Missing or malformed Authorization header"),
                },
                headers={"WWW-Authenticate": www_auth},
            )

        token = auth_header[len("Bearer ") :]

        try:
            claims = jwt.decode(
                token,
                signing_secret,
                algorithms=["HS256"],
                options={"require_exp": True},
                issuer=issuer,
            )
            email = claims.get("email", "unknown")
            print(f"AUTH OK: {email}", file=sys.stderr)
        except JWTError as exc:
            print(
                f"AUTH REJECT: JWT invalid — {exc}",
                file=sys.stderr,
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "error_description": str(exc),
                },
                headers={"WWW-Authenticate": www_auth},
            )

        return await call_next(request)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def oauth_authorization_server(
    request: Request,
) -> Response:
    issuer = os.getenv("JWT_ISSUER", "https://auth.nthparallel.com")
    return JSONResponse(
        {
            "issuer": issuer,
            "authorization_endpoint": (f"{issuer}/oauth/authorize"),
            "token_endpoint": f"{issuer}/oauth/token",
            "registration_endpoint": f"{issuer}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
    )


async def oauth_protected_resource(
    request: Request,
) -> Response:
    issuer = os.getenv("JWT_ISSUER", "https://auth.nthparallel.com")
    base_url = _get_base_url(request)
    return JSONResponse(
        {
            "resource": base_url,
            "authorization_servers": [issuer],
        }
    )


async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# App configuration
# ---------------------------------------------------------------------------


def create_app(mcp_server: "FastMCP") -> "Starlette":  # noqa: F821
    """Create the MCP Starlette app with JWT auth and
    well-known endpoints added directly."""

    # Get the SDK's Starlette app (has /mcp route built in)
    app = mcp_server.streamable_http_app()

    # Add our routes at the beginning so they match first
    custom_routes = [
        Route(
            "/.well-known/oauth-authorization-server",
            oauth_authorization_server,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource/{path:path}",
            oauth_protected_resource,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource",
            oauth_protected_resource,
            methods=["GET"],
        ),
        Route("/health", health, methods=["GET"]),
    ]
    app.routes[0:0] = custom_routes

    # Add middleware to rewrite /mcp to /mcp/ so the SDK's
    # Mount("/mcp") matches without a 307 redirect (which
    # Claude.ai's MCP client doesn't follow on POST).
    class TrailingSlashMiddleware(BaseHTTPMiddleware):
        async def dispatch(
            self,
            request: Request,
            call_next: RequestResponseEndpoint,
        ) -> Response:
            if request.url.path == "/mcp":
                request.scope["path"] = "/mcp/"
            return await call_next(request)

    app.add_middleware(TrailingSlashMiddleware)

    # Add JWT middleware (runs before trailing slash rewrite)
    app.add_middleware(JWTAuthMiddleware)

    return app
