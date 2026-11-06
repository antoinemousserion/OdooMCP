# Odoo MCP

A **read-only** [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that lets AI coding assistants (Cursor, Claude, etc.) **explore the Odoo source code** (community + enterprise) **without cloning it into your project**.

## What this is / what this is not

| This MCP **is** | This MCP is **not** |
|-----------------|---------------------|
| A local index of the **Odoo codebase** (Python, XML, JS, …) | A connector to a **running Odoo instance** (XML-RPC / JSON-RPC / HTTP API) |
| Tools to **search, read, and navigate** Odoo framework & addons | A way to **create records, call ORM methods, or query a database** |
| Meant for **developing custom modules** when you only have your project on disk (typical on **Odoo.sh**) | An Odoo bot / chat assistant for end users |

**Typical use:** on Odoo.sh (or any custom-module-only repo), the full Odoo tree is not in your workspace. Point Cursor at this MCP for the matching version (`odoo-v17`, `odoo-v18`, …) so the agent can look up how standard models, methods, and views are implemented — without polluting your git repo with `odoo/` or `enterprise/`.

## Features

- **One Docker container per Odoo version** — start only the version you need
- **Automatic Git clone/update** on container startup (community + enterprise)
- **Read-only access** — no file writes, path traversal protection
- **Fast search** via ripgrep, with Odoo-specific helpers (models, fields, modules)
- **Persistent local storage** — cloned code is cached on your machine (separate from your project)

## Supported versions

| Version | Docker profile | MCP port |
|---------|----------------|----------|
| 15.0    | `v15`          | 8015     |
| 16.0    | `v16`          | 8016     |
| 17.0    | `v17`          | 8017     |
| 19.0    | `v19`          | 8019     |

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose)
- GitHub account with access to [`odoo/enterprise`](https://github.com/odoo/enterprise) (Odoo partner or Enterprise subscription)
- An MCP-compatible client (e.g. [Cursor](https://cursor.com))

## Quick start

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/OdooMCP.git
cd OdooMCP
cp .env.example .env
```

Edit `.env`:

```env
GITHUB_TOKEN=ghp_your_token_here
ODOO_VOLUME_PATH=C:/path/to/your/odoo-cache
CLONE_ENTERPRISE=true
```

> **Windows tip:** use forward slashes in `ODOO_VOLUME_PATH` (e.g. `C:/Users/you/Documents/MCP Odoo`).

### 2. Start containers (interactive)

```powershell
.\scripts\start-odoo-mcp.ps1
```

The script shows an interactive checklist (Odoo 15–19, all selected by default). Use **↑/↓** to navigate, **Space** to toggle, **Enter** to confirm. If code already exists, it asks whether to force a fresh Git clone (**N** by default — Enter keeps existing code).

**Other commands:**

```powershell
.\scripts\start-odoo-mcp.ps1 -Logs -Version 19   # follow logs
.\scripts\start-odoo-mcp.ps1 -Stop -Version 19   # stop a container
```

**Docker Compose directly:**

```bash
docker compose --profile v19 up -d --build --force-recreate
```

The MCP server code is baked into the image (only the Odoo source is a volume): after changing `mcp-server/`, keep `--build --force-recreate`, otherwise the running containers may keep the old image. Then reconnect the MCP server in your client (e.g. `/mcp` in Claude Code) so it reloads the tool list.

On first run, the container clones Odoo community (and enterprise if configured). This can take several minutes.

Cloned code is stored under:

```
${ODOO_VOLUME_PATH}/
├── v18/
│   ├── community/
│   └── enterprise/
├── v17/
│   ├── community/
│   └── enterprise/
└── ...
```

### 3. Connect Cursor

Add the MCP server in **Cursor Settings → MCP** (see `cursor-mcp.example.json`):

```json
{
  "mcpServers": {
    "odoo-v19": {
      "url": "http://localhost:8019/sse"
    }
  }
}
```

Enable **only the version matching your current project** (same major as your Odoo.sh branch). The agent will use tools like `search_code` / `find_model` against that codebase — not against your live database.

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_TOKEN` | GitHub PAT for cloning `odoo/enterprise` | — |
| `ODOO_VOLUME_PATH` | Local folder for cloned Odoo code | `C:/Users/antoi/Documents/MCP Odoo` |
| `CLONE_ENTERPRISE` | Clone enterprise repo (`true` / `false`) | `true` |
| `GIT_DEPTH` | Shallow clone depth (`1` = fast, `0` = full history) | `1` |

### GitHub token scopes

Your GitHub account must be authorized on `odoo/enterprise` (via the Odoo partner portal or your Enterprise subscription).

**Classic PAT (recommended):**

- Scope: **`repo`** (required to clone private repositories)

**Fine-grained PAT:**

- Repository: `odoo/enterprise` only
- Permissions: **Contents: Read-only**, **Metadata: Read-only**

Create a token at: https://github.com/settings/tokens

## MCP tools

All tools are **read-only** and operate on the **cloned Odoo source tree** (files on disk). There is no tool to connect to an Odoo URL, database, or API.

| Tool | Description |
|------|-------------|
| `search_code` | Regex search via ripgrep. Scope: module name (e.g. `sale`), `framework` (`odoo/orm`, `odoo/fields`, `odoo/tools`…), `community`, `enterprise`, or `all` (addons + framework). `path=` targets one folder or file (e.g. `odoo/orm/fields_relational.py`, `addons/web/static/lib/Chart`). Tests, i18n and `static/lib` are excluded by default (`include_tests=True`, `include_vendor=True`, or a `path`/`glob` that targets them) |
| `read_file` | Read a file with line numbers (`offset` / `limit`) |
| `find_model` | Find where a model is defined or inherited (`_name` / `_inherit`) |
| `find_field` | Find field definitions: type, compute, related, store (pass `model_name`) |
| `get_module_info` | Return `__manifest__.py` and module structure |
| `find_method_overrides` | All Python overrides of a method + `super()` analysis and load order (slow, 5–10 s) |

## Common commands

```powershell
# Interactive startup (recommended)
.\scripts\start-odoo-mcp.ps1

# View logs
.\scripts\start-odoo-mcp.ps1 -Logs -Version 18

# Stop
.\scripts\start-odoo-mcp.ps1 -Stop -Version 18

# Or with Docker Compose
docker compose --profile v19 up -d --build --force-recreate
docker compose logs -f odoo-mcp-v19
docker compose --profile v19 down
```

## Adding a new Odoo version

1. Copy a service block in `docker-compose.yml` (e.g. `odoo-mcp-v20` with port `8020`, profile `v20`, `ODOO_VERSION: "20"`)
2. Add the version to `$AvailableVersions` in `scripts/start-odoo-mcp.ps1`
3. Add the corresponding entry in your Cursor MCP config

## Security

- The MCP server is **strictly read-only** — no write operations are exposed
- File access is restricted to the cloned `community/` and `enterprise/` directories
- Store `GITHUB_TOKEN` in `.env` only (never commit it — `.env` is gitignored)
- Containers run locally on your dev machine — no data is sent to external services

## Project structure

```
OdooMCP/
├── docker-compose.yml       # One service per Odoo version
├── .env.example             # Configuration template
├── cursor-mcp.example.json  # Cursor MCP config example
├── scripts/
│   └── start-odoo-mcp.ps1   # Helper script (Windows)
└── mcp-server/
    ├── Dockerfile
    ├── entrypoint.sh        # Git clone/update on startup
    └── odoo_mcp/            # MCP server (Python)
```
