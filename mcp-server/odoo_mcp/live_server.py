"""Serveur MCP readonly pour explorer la donnée d'une instance Odoo (17, 18, 19)."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from odoo_mcp.timing import log_tool_duration, setup_logging
from odoo_mcp.tools import live as live_tools

setup_logging()

_port = int(os.environ.get("MCP_PORT", "8900"))
# Hôtes acceptés dans l'en-tête Host (protection contre le DNS rebinding depuis un navigateur).
_extra_hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]

mcp = FastMCP(
    name="Odoo Live",
    instructions=(
        "Read-only access to the DATA of live Odoo instances (17, 18, 19). No tool can create, write or delete.\n\n"
        "Every tool takes `instance`: a profile name from list_instances. URLs and API keys live in a config file "
        "on the user's machine — never ask for them, and never pass a URL.\n\n"
        "WORKFLOW:\n"
        "1. list_instances — available profiles and their Odoo version.\n"
        "2. get_fields(instance, model, query=...) — field names, types and `relation` (target model of a relational field).\n"
        "3. search_records(instance, model, domain, fields) — always pass the `fields` you need.\n"
        "4. Follow a relation: many2one values come back as [id, display_name], x2many as a list of ids; "
        "then read_records(instance, <relation model>, ids, fields).\n"
        "5. count_records / group_records for volumes and breakdowns before reading rows.\n\n"
        "Results depend on the access rights of the API key's user; an AccessError means that user cannot read the model.\n"
        "For how a model or method is implemented, use the odoo-vXX source-code servers instead."
    ),
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=_port,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["localhost:*", "127.0.0.1:*", *_extra_hosts],
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
    ),
)


@mcp.tool()
@log_tool_duration
def list_instances() -> str:
    """List the configured Odoo instances (profile name, URL, database, detected version). Never returns API keys.

    Start here: the `instance` argument of every other tool is one of these profile names.
    """
    return live_tools.list_instances()


@mcp.tool()
@log_tool_duration
def list_models(instance: str, query: str = "", limit: int = 50) -> str:
    """Find models by technical name or label (e.g. query='sale'). Needs administration rights on the instance.

    If it fails with AccessError and you already know the model name, go straight to get_fields.
    """
    return live_tools.list_models(instance, query=query, limit=limit)


@mcp.tool()
@log_tool_duration
def get_fields(instance: str, model: str, query: str = "", fields: list[str] | None = None) -> str:
    """Fields of a model as seen by the instance (includes custom and Studio fields): type, label, relation, selection.

    query filters on field name or label (e.g. query='partner'); fields restricts to exact names.
    `relation` is the target model of a many2one/one2many/many2many field.
    """
    return live_tools.get_fields(instance, model, query=query, fields=fields)


@mcp.tool()
@log_tool_duration
def search_records(
    instance: str,
    model: str,
    domain: list | None = None,
    fields: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
    order: str | None = None,
    include_archived: bool = False,
) -> str:
    """Search records with an Odoo domain and read the given fields.

    domain: Odoo domain as a JSON list, e.g. [["state", "=", "sale"], ["partner_id.country_id.code", "=", "FR"]].
    fields: list of field names. Omitted → id and display_name only. ['*'] → every field (slow, large).
    limit: max 200. The header says has_more; page with offset.
    order: e.g. 'create_date desc'.
    include_archived: also return records with active=False.

    Binary fields return their size, not their content.

    Examples:
        model='res.partner', domain=[["is_company", "=", true]], fields=['name', 'email', 'country_id']
        model='sale.order', domain=[["date_order", ">=", "2026-01-01"]], fields=['name', 'amount_total'], order='amount_total desc'
    """
    return live_tools.search_records(
        instance,
        model,
        domain=domain,
        fields=fields,
        limit=limit,
        offset=offset,
        order=order,
        include_archived=include_archived,
    )


@mcp.tool()
@log_tool_duration
def read_records(instance: str, model: str, ids: list[int], fields: list[str] | None = None) -> str:
    """Read records by id (max 200). Use it to follow a relation: take the ids from a relational field, and the
    target model from get_fields (`relation`).

    fields: omitted → id and display_name only. ['*'] → every field.
    """
    return live_tools.read_records(instance, model, ids, fields=fields)


@mcp.tool()
@log_tool_duration
def count_records(instance: str, model: str, domain: list | None = None, include_archived: bool = False) -> str:
    """Count the records matching a domain. Cheap: use it before reading a large set."""
    return live_tools.count_records(instance, model, domain=domain, include_archived=include_archived)


@mcp.tool()
@log_tool_duration
def group_records(
    instance: str,
    model: str,
    groupby: list[str],
    domain: list | None = None,
    aggregates: list[str] | None = None,
    limit: int = 80,
    include_archived: bool = False,
) -> str:
    """Group records and aggregate (SQL GROUP BY). The count of each group is always returned.

    groupby: e.g. ['state'], ['partner_id'], ['date_order:month'] (day, week, month, quarter, year).
    aggregates: e.g. ['amount_total:sum', 'amount_total:avg'] (sum, avg, min, max, count_distinct…).

    The shape of the rows differs slightly between Odoo 17/18 (read_group) and 19 (formatted_read_group).
    """
    return live_tools.group_records(
        instance,
        model,
        groupby,
        domain=domain,
        aggregates=aggregates,
        limit=limit,
        include_archived=include_archived,
    )


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "sse")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
