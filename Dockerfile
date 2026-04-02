FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen

COPY . .

ENV MCP_TRANSPORT=http
ENV PORT=8000

CMD ["uv", "run", "python", "main_quickbooks_mcp.py"]
