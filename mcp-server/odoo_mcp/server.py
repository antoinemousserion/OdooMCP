"""Serveur MCP readonly pour explorer une codebase Odoo."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from odoo_mcp.config import get_odoo_paths
from odoo_mcp.security import PathSecurityError
from odoo_mcp.tools import explore as explore_tools
from odoo_mcp.tools import fields_tools
from odoo_mcp.tools import methods as methods_tools
from odoo_mcp.tools import modules_tools
from odoo_mcp.tools import odoo as odoo_tools
from odoo_mcp.tools import search as search_tools
from odoo_mcp.tools import views as views_tools

_paths = get_odoo_paths()
_version = _paths.version

mcp = FastMCP(
    name=f"Odoo {_version}",
    instructions=(
        f"Read-only Odoo {_version} MCP server.\n"
        "High-level: explore_model, explore_method, explore_module (one-shot summaries).\n"
        "Models: find_model, list_model_fields, find_field(model_name=...), "
        "get_model_inheritance_chain, find_model_mixins, find_compute_dependencies.\n"
        "Methods: find_method_overrides, trace_method_chain, find_method_callers.\n"
        "Views/UI: find_xml_action, find_view_for_model, get_button_context, "
        "find_window_action, resolve_view_inheritance.\n"
        "Modules: get_module_dependencies, get_dependent_modules, get_module_models.\n"
        "Generic: search_code (query/pattern), glob_files, read_file."
    ),
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8000")),
)


@mcp.tool()
def get_codebase_status() -> str:
    """Git clone status (community/enterprise, branch, file counts, enterprise warning)."""
    return odoo_tools.get_codebase_status(_paths)


@mcp.tool()
def search_code(
    pattern: str | None = None,
    query: str | None = None,
    scope: str = "all",
    glob_pattern: str | None = None,
    glob: str | None = None,
    file_type: str | None = "py",
    context_lines: int = 0,
    max_results: int = 100,
    case_insensitive: bool = False,
) -> str:
    """Regex search via ripgrep. Aliases: query->pattern, glob->glob_pattern.

    Args:
        pattern: Regex pattern (ripgrep). Alias: query.
        query: Alias for pattern.
        scope: 'all', 'community', 'enterprise', or module name (e.g. 'sale').
        glob_pattern: Optional glob filter (e.g. '**/models/*.py'). Alias: glob.
        glob: Alias for glob_pattern.
        file_type: ripgrep type (py, xml, js). None for all files.
        context_lines: Context lines before/after each match.
        max_results: Max matches returned.
        case_insensitive: Case-insensitive search.
    """
    return search_tools.search_code(
        _paths,
        pattern=pattern,
        query=query,
        scope=scope,
        glob_pattern=glob_pattern,
        glob=glob,
        file_type=file_type,
        context=context_lines,
        max_results=max_results,
        case_insensitive=case_insensitive,
    )


@mcp.tool()
def glob_files(
    pattern: str | None = None,
    glob_pattern: str | None = None,
    scope: str = "all",
    max_results: int = 100,
) -> str:
    """Find files by glob. Accepts 'pattern' or 'glob_pattern'.

    Args:
        pattern: Glob (e.g. '**/views/*.xml'). Alias: glob_pattern.
        glob_pattern: Alias for pattern.
        scope: 'all', 'community', 'enterprise', or module name.
        max_results: Max files returned.
    """
    return search_tools.glob_files(
        _paths,
        pattern=pattern,
        glob_pattern=glob_pattern,
        scope=scope,
        max_results=max_results,
    )


@mcp.tool()
def read_file(
    path: str,
    offset: int = 1,
    limit: int = 200,
) -> str:
    """Read source file with line numbers. Accepts community/..., workspace_hint, or addons/... paths.

    Args:
        path: File path — any of: addons/sale/..., community/addons/sale/..., odoo_v19/addons/sale/...
        offset: Start line (1-indexed).
        limit: Max lines.
    """
    try:
        return search_tools.read_file(_paths, path, offset=offset, limit=limit)
    except PathSecurityError as exc:
        return f"Error: {exc}"


@mcp.tool()
def list_directory(
    path: str = "",
    max_entries: int = 200,
) -> str:
    """List directory contents in the Odoo codebase."""
    try:
        return search_tools.list_directory(_paths, path, max_entries=max_entries)
    except PathSecurityError as exc:
        return f"Error: {exc}"


@mcp.tool()
def list_addons(edition: str = "all") -> str:
    """List Odoo modules detected via __manifest__.py."""
    return odoo_tools.list_addons(_paths, edition=edition)


# --- Super-tools ---


@mcp.tool()
def explore_model(model_name: str) -> str:
    """One-shot model overview: definition, fields, mixins, inheritance, views, action methods.

    Args:
        model_name: Technical model (e.g. 'sale.order').
    """
    return explore_tools.explore_model(_paths, model_name)


@mcp.tool()
def explore_method(model_name: str, method_name: str) -> str:
    """One-shot method analysis: overrides, execution chain, callers (Python + XML).

    Args:
        model_name: Technical model (e.g. 'sale.order').
        method_name: Method name (e.g. 'action_confirm').
    """
    return explore_tools.explore_method(_paths, model_name, method_name)


@mcp.tool()
def explore_module(module_name: str) -> str:
    """One-shot module overview: manifest, dependencies, dependents, models touched.

    Args:
        module_name: Module name (e.g. 'sale_stock').
    """
    return explore_tools.explore_module(_paths, module_name)


# --- Models & fields ---


@mcp.tool()
def find_model(
    model_name: str | None = None,
    model: str | None = None,
    max_results: int = 30,
) -> str:
    """Find Odoo model definitions and inheritances. Returns structured JSON.

    Args:
        model_name: Technical model name (e.g. 'sale.order'). Alias: model.
        model: Alias for model_name.
        max_results: Max results per category (definitions / inherits).
    """
    return odoo_tools.find_model(_paths, model_name=model_name, model=model, max_results=max_results)


@mcp.tool()
def find_field(
    field_name: str,
    model_name: str | None = None,
    module_hint: str | None = None,
) -> str:
    """Find Odoo field definitions with type, related, compute, store, tracking.

    Prefer model_name to avoid noise on generic fields (state, partner_id, name).

    Args:
        field_name: Field name (e.g. 'amount_total', 'state').
        model_name: Technical model (e.g. 'sale.order').
        module_hint: Odoo module name (e.g. 'sale'). Optional if model_name is set.
    """
    return odoo_tools.find_field(
        _paths,
        field_name,
        module_hint=module_hint,
        model_name=model_name,
    )


@mcp.tool()
def list_model_fields(model_name: str, max_fields: int = 200) -> str:
    """List all fields on a model (base + inherits): type, computed, related, tracked.

    Args:
        model_name: Technical model (e.g. 'sale.order').
        max_fields: Max fields returned.
    """
    return fields_tools.list_model_fields(_paths, model_name, max_fields=max_fields)


@mcp.tool()
def find_compute_dependencies(model_name: str, field_name: str) -> str:
    """Find @api.depends for a computed field — why it may not recompute.

    Args:
        model_name: Technical model (e.g. 'sale.order').
        field_name: Computed field name (e.g. 'amount_total').
    """
    return fields_tools.find_compute_dependencies(_paths, model_name, field_name)


@mcp.tool()
def get_model_inheritance_chain(model_name: str) -> str:
    """Build the _inherit extension chain for a model (MRO-style exploration).

    Args:
        model_name: Technical model name (e.g. 'sale.order').
    """
    return odoo_tools.get_model_inheritance_chain(_paths, model_name)


@mcp.tool()
def find_model_mixins(model_name: str) -> str:
    """Detect mixins inherited by a model (mail.thread, portal.mixin, etc.).

    Args:
        model_name: Technical model (e.g. 'sale.order').
    """
    return fields_tools.find_model_mixins(_paths, model_name)


# --- Methods ---


@mcp.tool()
def find_method_overrides(
    model_name: str,
    method_name: str,
    max_results: int = 50,
) -> str:
    """List every Python override of a method with super() analysis and side-effect calls.

    Args:
        model_name: Technical model (e.g. 'sale.order').
        method_name: Python method name (e.g. 'action_confirm', '_compute_amount').
        max_results: Max overrides returned.
    """
    return methods_tools.find_method_overrides(_paths, model_name, method_name, max_results=max_results)


@mcp.tool()
def trace_method_chain(model_name: str, method_name: str) -> str:
    """Reconstruct override execution order (load rank + super() calls).

    Args:
        model_name: Technical model (e.g. 'sale.order').
        method_name: Method name (e.g. 'action_confirm').
    """
    return methods_tools.trace_method_chain(_paths, model_name, method_name)


@mcp.tool()
def find_method_callers(
    model_name: str,
    method_name: str,
    max_results: int = 50,
) -> str:
    """Who calls this method? Python callers + XML buttons (type=object).

    Args:
        model_name: Technical model (e.g. 'sale.order').
        method_name: Method name (e.g. 'action_confirm').
        max_results: Max callers returned.
    """
    return methods_tools.find_method_callers(_paths, model_name, method_name, max_results=max_results)


# --- Views & UI ---


@mcp.tool()
def find_xml_action(
    action_name: str,
    model: str | None = None,
    max_results: int = 30,
) -> str:
    """Find XML buttons/actions calling a Python method.

    Args:
        action_name: Button/action name (e.g. 'action_confirm').
        model: Optional model filter (e.g. 'sale.order').
        max_results: Max results.
    """
    return odoo_tools.find_xml_action(_paths, action_name, model=model, max_results=max_results)


@mcp.tool()
def find_view_for_model(model_name: str, max_results: int = 50) -> str:
    """List form/tree/kanban/search views for a model.

    Args:
        model_name: Technical model (e.g. 'sale.order').
        max_results: Max views returned.
    """
    return views_tools.find_view_for_model(_paths, model_name, max_results=max_results)


@mcp.tool()
def get_button_context(model_name: str, button_name: str) -> str:
    """Return context, groups, invisible, states for an XML button.

    Args:
        model_name: Technical model (e.g. 'sale.order').
        button_name: Button name attribute (e.g. 'action_confirm').
    """
    return views_tools.get_button_context(_paths, model_name, button_name)


@mcp.tool()
def find_window_action(model_name: str, max_results: int = 30) -> str:
    """Find ir.actions.act_window entries for a model.

    Args:
        model_name: Technical model (e.g. 'sale.order').
        max_results: Max actions returned.
    """
    return views_tools.find_window_action(_paths, model_name, max_results=max_results)


@mcp.tool()
def resolve_view_inheritance(
    view_xml_id: str | None = None,
    file_hint: str | None = None,
) -> str:
    """Resolve inherit_id chain for a view (base view → extensions).

    Args:
        view_xml_id: XML id (e.g. 'sale.view_order_form').
        file_hint: Path to a view XML file to start from.
    """
    return views_tools.resolve_view_inheritance(_paths, view_xml_id=view_xml_id, file_hint=file_hint)


# --- Modules ---


@mcp.tool()
def find_references(
    model: str,
    symbol: str,
    max_results: int = 50,
) -> str:
    """Find Python/XML references to a model method or symbol.

    Args:
        model: Model name (e.g. 'sale.order').
        symbol: Method or field name (e.g. 'action_confirm').
        max_results: Max references.
    """
    return odoo_tools.find_references(_paths, model, symbol, max_results=max_results)


@mcp.tool()
def get_module_dependencies(module_name: str) -> str:
    """Return depends list from a module __manifest__.py.

    Args:
        module_name: Module name (e.g. 'sale_stock').
    """
    return odoo_tools.get_module_dependencies(_paths, module_name)


@mcp.tool()
def get_dependent_modules(module_name: str) -> str:
    """List modules that depend on this one (impact analysis).

    Args:
        module_name: Module name (e.g. 'sale').
    """
    return modules_tools.get_dependent_modules(_paths, module_name)


@mcp.tool()
def get_module_models(module_name: str) -> str:
    """List models defined or extended in a module.

    Args:
        module_name: Module name (e.g. 'sale_stock').
    """
    return modules_tools.get_module_models(_paths, module_name)


@mcp.tool()
def get_module_info(module_name: str) -> str:
    """Return __manifest__.py and module structure."""
    return odoo_tools.get_module_info(_paths, module_name)


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "sse")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
