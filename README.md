# QuickBooks MCP Server

A Model Context Protocol (MCP) server for querying QuickBooks data using natural language. Supports both local stdio mode (Claude Desktop) and remote HTTP mode (Claude.ai) with OAuth 2.0 JWT authentication.

## Requirements

- Python 3.12 or higher
- [uv](https://docs.astral.sh/uv/) package manager

## Environment Setup

Create a `.env` file from the template:

```bash
cp env_template.txt .env
```

Fill in your QuickBooks API credentials:

```
QUICKBOOKS_CLIENT_ID=your_actual_client_id
QUICKBOOKS_CLIENT_SECRET=your_actual_client_secret
QUICKBOOKS_REFRESH_TOKEN=your_actual_refresh_token
QUICKBOOKS_COMPANY_ID=your_actual_company_id
QUICKBOOKS_ENV=sandbox
```

**Note:** The `.env` file is ignored by git.

## Local Mode (stdio)

For use with Claude Desktop.

### 1. Install uv

- macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

### 2. Configure Claude Desktop

Go to Settings > Developer > Edit Config and add:

```json
{
  "mcpServers": {
    "QuickBooks": {
      "command": "uv",
      "args": [
        "--directory",
        "<absolute_path_to_quickbooks_mcp_folder>",
        "run",
        "main_quickbooks_mcp.py"
      ]
    }
  }
}
```

Relaunch Claude Desktop. The first launch may take 10-20 seconds to install dependencies and download the latest QuickBooks API documentation.

## Remote Mode (HTTP)

For deployment as a remote MCP server accessible by Claude.ai.

### Additional Environment Variables

```
MCP_TRANSPORT=http
SERVER_BASE_URL=https://your-public-url.example.com
AUTH_WORKER_BASE_URL=https://auth.nthparallel.com
AUTH_WORKER_CLIENT_ID=your-registered-client-id
PORT=8000
```

### Prerequisites

Register a client for this server in the auth worker's KV store before deploying. Set the resulting client ID as `AUTH_WORKER_CLIENT_ID`.

### Running Locally

```bash
MCP_TRANSPORT=http SERVER_BASE_URL=http://localhost:8000 AUTH_WORKER_CLIENT_ID=your-id uv run python main_quickbooks_mcp.py
```

### Docker Deployment (Railway)

```bash
docker build -t quickbooks-mcp .
docker run -p 8000:8000 \
  -e QUICKBOOKS_CLIENT_ID=... \
  -e QUICKBOOKS_CLIENT_SECRET=... \
  -e QUICKBOOKS_REFRESH_TOKEN=... \
  -e QUICKBOOKS_COMPANY_ID=... \
  -e QUICKBOOKS_ENV=production \
  -e SERVER_BASE_URL=https://your-public-url.example.com \
  -e AUTH_WORKER_CLIENT_ID=... \
  quickbooks-mcp
```

### Endpoints

| Path | Auth | Description |
|------|------|-------------|
| `/mcp` | Bearer token | MCP streamable-HTTP transport |
| `/.well-known/oauth-authorization-server` | None | OAuth server metadata |
| `/register` | None | Dynamic client registration |
| `/authorize` | None | OAuth authorization endpoint |
| `/token` | None | OAuth token endpoint |
| `/health` | None | Health check |

### Authentication

The server uses the MCP SDK's built-in OAuth 2.0 authorization server with dynamic client registration. User authentication is delegated to the auth worker at `auth.nthparallel.com`, which handles Google OAuth login. Claude.ai discovers the OAuth server via `/.well-known/oauth-authorization-server`, registers dynamically, and completes the authorization code flow with PKCE. All authenticated users share the same QuickBooks credentials.

## Usage Examples

**Query Accounts**
```text
Get all accounts from QuickBooks.
```

**Query Bills**
```text
Get all bills from QuickBooks created after 2024-01-01.
```

**Query Customers**
```text
Get all customers from QuickBooks.
```
