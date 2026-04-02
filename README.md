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
JWT_SIGNING_SECRET=your_jwt_signing_secret
JWT_ISSUER=https://auth.nthparallel.com
PORT=8000
```

### Running Locally

```bash
MCP_TRANSPORT=http uv run python main_quickbooks_mcp.py
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
  -e JWT_SIGNING_SECRET=... \
  quickbooks-mcp
```

### Endpoints

| Path | Auth | Description |
|------|------|-------------|
| `/mcp` | JWT required | MCP streamable-HTTP transport |
| `/health` | None | Health check |
| `/.well-known/oauth-authorization-server` | None | OAuth discovery |
| `/.well-known/oauth-protected-resource` | None | Protected resource metadata |

### Authentication

The server validates HS256 JWT Bearer tokens issued by the configured OAuth authorization server. Claude.ai discovers the OAuth server via the `/.well-known/oauth-authorization-server` endpoint. All authenticated users share the same QuickBooks credentials configured on the server.

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
