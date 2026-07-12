FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY odoo_mcp ./odoo_mcp

RUN pip install --no-cache-dir .

COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

ENV MCP_TRANSPORT=sse
ENV MCP_HOST=0.0.0.0
ENV ODOO_DATA_ROOT=/data/odoo

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
