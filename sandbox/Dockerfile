# syntax=docker/dockerfile:1
# =========================================================
# Sandbox image for code review / lint / test execution
# Tools: Python 3.11, Node 20, ruff, pytest, Playwright
# No secrets or credentials should be passed into this image.
# =========================================================
FROM python:3.11-slim AS base

# Install system deps + Node.js 20
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ca-certificates gnupg && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install Python tools
RUN pip install --no-cache-dir ruff pytest mcp

# Install Playwright (chromium only, with system deps)
RUN npx playwright install --with-deps chromium

# Non-root user
RUN groupadd --gid 1001 sandbox && \
    useradd --uid 1001 --gid 1001 --create-home --shell /bin/bash sandbox

# Copy MCP server code
COPY mcp_server.py /opt/sandbox/mcp_server.py

# Workspace directory (tmpfs will be mounted at runtime)
RUN mkdir -p /workspace && chown sandbox:sandbox /workspace

USER sandbox
WORKDIR /workspace

# Default entrypoint: start MCP server on stdio
ENTRYPOINT ["python", "/opt/sandbox/mcp_server.py"]
