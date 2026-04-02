# Decision Log

## 2026-04-02 — HTTP transport via FastAPI wrapper instead of modifying FastMCP

**Context:** Adding streamable-HTTP transport to the existing stdio MCP server.

**Decision:** Created a separate `server.py` that wraps the existing `FastMCP` instance in a FastAPI app, rather than modifying `main_quickbooks_mcp.py` extensively.

**Rationale:** The existing module uses `exec()` and dynamic tool registration at import time. Keeping `main_quickbooks_mcp.py` as the tool definition module and adding a thin FastAPI wrapper preserves backward compatibility and keeps concerns separated (tools vs transport/auth).

**Impact:** `main_quickbooks_mcp.py` remains the single entry point for both modes. In HTTP mode it imports `server.py` lazily. The `mcp` instance is shared by reference.

## 2026-04-02 — Switch from custom JWT middleware to MCP SDK built-in OAuth

**Context:** The initial implementation used a custom FastAPI wrapper with JWT middleware. Claude.ai requires dynamic client registration (RFC 7591) as part of the MCP OAuth flow. The external auth worker doesn't support dynamic registration, and our custom middleware didn't either.

**Decision:** Use the MCP SDK's built-in `OAuthAuthorizationServerProvider` to make the MCP server its own OAuth authorization server. User authentication is delegated to the auth worker via the double-OAuth pattern — the SDK handles client registration, token exchange, and bearer auth, while we redirect to the auth worker for Google authentication.

**Rationale:** The MCP SDK has a complete, spec-compliant OAuth implementation including dynamic client registration, PKCE, and bearer token middleware. Using it avoids reimplementing the MCP OAuth spec and ensures compatibility with Claude.ai's client requirements.

**Impact:** `server.py` is now an `OAuthAuthorizationServerProvider` implementation instead of FastAPI middleware. FastAPI and python-jose are no longer dependencies. The auth provider is patched onto the existing `FastMCP` instance before `streamable_http_app()` is called, avoiding tool re-registration. A new `AUTH_WORKER_CLIENT_ID` must be registered in the auth worker's KV.

## 2026-04-02 — Shared QuickBooks credentials across all authenticated users

**Context:** The JWT auth identifies users by email, but QuickBooks credentials are server-side environment variables.

**Decision:** All authenticated users share the same QuickBooks session. The JWT email is logged but does not affect QBO access.

**Rationale:** The QuickBooks integration uses a single company's OAuth refresh token. Per-user QBO auth would require a completely different architecture. The JWT auth serves as a gatekeeper, not a user-isolation mechanism.

**Impact:** Any user with a valid JWT from the configured issuer has full access to the QuickBooks company data.
