# Odoo MCP

A **read-only** [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that lets AI coding assistants (Cursor, Claude, etc.) **explore the Odoo source code** (community + enterprise) **without cloning it into your project**.

A separate, optional endpoint, [`odoo-live`](#odoo-live-explore-the-data-of-an-instance), reads the **data** of a running Odoo instance (17, 18, 19), also read-only.

## What this is / what this is not

| This MCP **is** | This MCP is **not** |
|-----------------|---------------------|
| A local index of the **Odoo codebase** (Python, XML, JS, …) | A way to **create, modify or delete records**, or to call arbitrary ORM methods |
| Tools to **search, read, and navigate** Odoo framework & addons | A general-purpose Odoo API client |
| Optionally, read-only tools to **explore the data** of a running instance (`odoo-live`) | A direct database (SQL) connection |
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
| 18.0    | `v18`          | 8018     |
| 19.0    | `v19`          | 8019     |
| live instance data (17–19) | `live` | 8900 |

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

The `odoo-vXX` servers are **read-only** and operate on the **cloned Odoo source tree** (files on disk). They never connect to an Odoo URL, database, or API; that is the job of the separate [`odoo-live`](#odoo-live-explore-the-data-of-an-instance) server.

| Tool | Description |
|------|-------------|
| `search_code` | Regex search via ripgrep. Scope: module name (e.g. `sale`), `framework` (`odoo/orm`, `odoo/fields`, `odoo/tools`…), `community`, `enterprise`, or `all` (addons + framework). `path=` targets one folder or file (e.g. `odoo/orm/fields_relational.py`, `addons/web/static/lib/Chart`). Tests, i18n and `static/lib` are excluded by default (`include_tests=True`, `include_vendor=True`, or a `path`/`glob` that targets them) |
| `read_file` | Read a file with line numbers (`offset` / `limit`) |
| `find_model` | Find where a model is defined or inherited (`_name` / `_inherit`) |
| `find_field` | Find field definitions: type, compute, related, store (pass `model_name`) |
| `get_module_info` | Return `__manifest__.py` and module structure |
| `find_method_overrides` | All Python overrides of a method + `super()` analysis and load order (slow, 5–10 s) |

## odoo-live: explore the data of an instance

A separate container that reads records, fields and relations from running Odoo instances (17, 18, 19) through Odoo's external API. It exposes no tool that can create, write or delete.

### Setup

1. Create `%USERPROFILE%\.odoo-mcp\instances.json` (see `instances.example.json`). `start-odoo-mcp.ps1` creates an empty one when you select "Odoo live".

   ```json
   {
     "client-x-staging": {
       "url": "https://client-x-staging.odoo.com",
       "db": "client-x-staging-1234567",
       "login": "readonly@client-x.com",
       "api_key": "..."
     }
   }
   ```

   | Key | Required | Notes |
   |-----|----------|-------|
   | `url` | yes | `https` only, except `localhost`, `127.0.0.1` and `host.docker.internal` (an Odoo running on your machine) |
   | `api_key` | yes | Created in Odoo under *My Profile → Account Security → New API Key* |
   | `db`, `login` | Odoo 17 and 18 | Optional on Odoo 19 (`db` is still needed if the server hosts several databases) |

2. Start the container: `docker compose --profile live up -d --build --force-recreate` (or tick "Odoo live" in `start-odoo-mcp.ps1`).
3. Add `"odoo-live": { "url": "http://localhost:8900/sse" }` to your MCP client.

**Changing a URL or a key:** edit `instances.json` and save. The file is re-read on the next tool call: no restart, no redeploy, no client reconnect. Set `ODOO_MCP_CONFIG_DIR` in `.env` to keep the file in another folder.

### Tools

Every tool takes `instance`, the profile name. No tool accepts a URL, a key or a method name.

| Tool | Description |
|------|-------------|
| `list_instances` | Configured profiles with URL, database and detected Odoo version. Never returns keys |
| `list_models` | Find models by name or label (needs administration rights on the instance) |
| `get_fields` | Fields of a model as the instance sees them, custom and Studio fields included: type, label, `relation`, selection |
| `search_records` | Search with an Odoo domain and read the chosen fields (max 200 rows per call, paged) |
| `read_records` | Read records by id; used to follow a relation |
| `count_records` | Count the records matching a domain |
| `group_records` | Group and aggregate (`read_group` on 17/18, `formatted_read_group` on 19) |

### How read-only is enforced

- The client refuses any ORM method other than `search_read`, `read`, `search_count`, `fields_get`, `read_group` and `formatted_read_group`, before sending anything.
- The call context is set by the server (`bin_size`, `active_test`); the assistant cannot supply one.
- **Use an API key that belongs to a dedicated Odoo user with read-only access rights.** That is the only guarantee on the instance side, and it also limits what the assistant can read: everything that user can see may end up in the conversation with your AI provider.
- On Odoo 17 and 18, obtaining the user id calls `common.authenticate`, which records a login (one `res.users.log` row) once per profile and container start. Nothing else is written. Odoo 19 uses `/json/2` with a bearer token and writes nothing.

### Compatibility

| Odoo | Endpoint | Authentication |
|------|----------|----------------|
| 17, 18 | `/jsonrpc` (`execute_kw`) | user id + API key |
| 19+ | `/json/2/<model>/<method>` | `Authorization: Bearer <key>` |

The version is detected from `/web/webclient/version_info`. On Odoo 18 and 19, API keys expire: an expired key shows up as an authentication error.

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

# odoo-live
.\scripts\start-odoo-mcp.ps1 -Logs -Version live
docker compose --profile live up -d --build --force-recreate
```

Tests (odoo-live only, run inside the image):

```bash
docker build -t odoo-mcp-test ./mcp-server
docker run --rm --entrypoint sh -v "$PWD/mcp-server/tests:/app/tests:ro" odoo-mcp-test -c "pip install -q pytest && python -m pytest /app/tests -p no:cacheprovider"
```

## Adding a new Odoo version

1. Copy a service block in `docker-compose.yml` (e.g. `odoo-mcp-v20` with port `8020`, profile `v20`, `ODOO_VERSION: "20"`)
2. Add the version to `$AvailableVersions` in `scripts/start-odoo-mcp.ps1` and `scripts/autostart-odoo-mcp.ps1`
3. Add the corresponding entry in your Cursor MCP config

## Security

- The MCP servers are **strictly read-only** — no write operations are exposed
- File access is restricted to the cloned `community/` and `enterprise/` directories
- Store `GITHUB_TOKEN` in `.env` only (never commit it — `.env` is gitignored)
- Containers run locally on your dev machine. The `odoo-vXX` servers send no data to external services

`odoo-live` is different: it calls the Odoo instances you configure, and the records it reads are returned to your AI assistant.

- API keys live only in `instances.json`, outside the repository, mounted read-only. They are never returned by a tool, never logged, and never pass through the conversation
- The container does not receive `.env` (so no `GITHUB_TOKEN`), and its port is published on `127.0.0.1` only
- `https` is required for remote instances, and HTTP redirects are not followed
- `instances.json` stores keys in clear text: keep it out of synced or shared folders

## Project structure

```
OdooMCP/
├── docker-compose.yml       # One service per Odoo version + odoo-mcp-live
├── .env.example             # Configuration template
├── cursor-mcp.example.json  # Cursor MCP config example
├── instances.example.json   # odoo-live profiles template
├── scripts/
│   ├── start-odoo-mcp.ps1       # Interactive start / logs / stop (Windows)
│   ├── autostart-odoo-mcp.ps1   # Starts ODOO_VERSIONS at logon
│   └── install-autostart.ps1    # Registers the scheduled task
└── mcp-server/
    ├── Dockerfile
    ├── entrypoint.sh        # Git clone/update on startup (odoo-vXX)
    ├── tests/               # odoo-live tests
    └── odoo_mcp/            # MCP servers (Python)
        ├── server.py        # odoo-vXX: source-code tools
        └── live_server.py   # odoo-live: instance data tools
```
