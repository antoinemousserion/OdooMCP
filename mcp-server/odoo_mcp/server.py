"""Serveur MCP readonly pour explorer une codebase Odoo."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from odoo_mcp.config import get_odoo_paths
from odoo_mcp.timing import log_tool_duration, setup_logging
from odoo_mcp.tools import methods as methods_tools
from odoo_mcp.tools import odoo as odoo_tools
from odoo_mcp.tools import search as search_tools
from odoo_mcp.tools.model_utils import load_module_depends

setup_logging()
_paths = get_odoo_paths()
_version = _paths.version

# Pré-charge le graphe de dépendances (~11 s au premier appel sinon).
load_module_depends(_paths)

mcp = FastMCP(
    name=f"Odoo {_version}",
    instructions=(
        f"Read-only Odoo {_version} MCP — minimal toolset.\n\n"
        "DEFAULT WORKFLOW (fast):\n"
        "1. search_code with scope=<module> (e.g. 'sale') — never scope='all' unless unavoidable.\n"
        "2. read_file on paths from search results.\n\n"
        "STRUCTURED HELPERS (prefer over parsing grep yourself):\n"
        "- find_model(model_name, scope=<module>) — definitions + _inherit extensions; scope limits scan.\n"
        "- find_field(field_name, model_name=...) — field type, compute, related, store.\n"
        "- get_module_info(module_name) — manifest depends + folder structure.\n\n"
        "SLOW / SPECIALIZED (5–10 s, use only when you need ALL overrides + super() analysis):\n"
        "- find_method_overrides(model_name, method_name) — do NOT call twice; "
        "for a quick locate use search_code(scope=module, glob='**/models/*.py', query='def action_confirm').\n\n"
        "NOT AVAILABLE — use search_code + read_file instead:\n"
        "views/buttons, callers, mixins, compute @depends, window actions, inheritance chain, glob → search_code/glob in query."
    ),
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8000")),
)


# --- Core (always prefer these) ---


@mcp.tool()
@log_tool_duration
def search_code(
    pattern: str | None = None,
    query: str | None = None,
    scope: str = "all",
    glob_pattern: str | None = None,
    glob: str | None = None,
    file_type: str | None = None,
    context_lines: int = 0,
    max_results: int = 30,
    case_insensitive: bool = False,
    include_tests: bool = False,
) -> str:
    """Primary exploration tool — ripgrep. ALWAYS set scope to a module name (e.g. 'sale') when possible.

    Searches ALL file types by default (py, xml, js, scss, csv…); narrow with file_type ('py', 'xml', 'js',
    comma-separated: 'py,xml') or glob. Excluded by default: tests/ and test_* modules (include_tests=True to
    search them), i18n/ translations, static/lib/ vendored JS. A glob mentioning 'test' or 'i18n' lifts the
    matching exclusion. The result header lists the active filters.

    Quote rules (pattern is passed to ripgrep as regex by default):
        Python single quotes in source: query="def action_confirm" or query='def action_confirm'
        XML double quotes: query='name=\"action_confirm\"' (escape inner doubles with backslash)
        XML single quotes: query="name='action_confirm'" (no escape needed inside doubles)
        Literal dots/special chars: use fixed_string via pattern with -F semantics is not exposed;
        escape regex metacharacters or match literally (e.g. sale\\.order).

    Examples:
        query='def action_confirm', scope='sale', glob='**/models/*.py'
        query='amount_total', scope='sale', glob='**/models/*.py'
        query='name=\"action_confirm\"', scope='sale', glob='**/views/**/*.xml'
        query='<field name=\"model\">sale.order</field>', scope='sale', file_type='xml'
        query='patch\\(', scope='sale', file_type='js'
        query='def test_.*confirm', scope='sale', include_tests=True
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
        include_tests=include_tests,
    )


@mcp.tool()
@log_tool_duration
def read_file(
    path: str,
    offset: int = 1,
    limit: int = 150,
) -> str:
    """Read source with line numbers. Use after search_code. Keep limit ≤150 unless necessary."""
    return search_tools.read_file(_paths, path, offset=offset, limit=limit)


# --- Odoo helpers (structured, scoped) ---


@mcp.tool()
@log_tool_duration
def find_model(
    model_name: str | None = None,
    model: str | None = None,
    scope: str = "all",
    max_results: int = 20,
) -> str:
    """Where a model is defined (_name) and extended (_inherit). Set scope to the home module (e.g. 'sale') for a fast scan.

    main_file = original definition. definitions[] includes definition_kind: 'canonical' (original definition)
    vs 'redeclaration' (_name + _inherit of the same model, i.e. an extension such as microsoft_calendar).
    """
    return odoo_tools.find_model(
        _paths, model_name=model_name, model=model, scope=scope, max_results=max_results
    )


@mcp.tool()
@log_tool_duration
def find_field(
    field_name: str,
    model_name: str | None = None,
    module_hint: str | None = None,
) -> str:
    """Field definition: type, related, compute, store, tracking.

    model_name is REQUIRED for useful results — without it the scan is slow (~10 s) and returns 100+ noisy hits.
    """
    return odoo_tools.find_field(
        _paths,
        field_name,
        module_hint=module_hint,
        model_name=model_name,
    )


@mcp.tool()
@log_tool_duration
def get_module_info(module_name: str) -> str:
    """Manifest (depends, version) + module folder structure. Scoped to one module — fast."""
    return odoo_tools.get_module_info(_paths, module_name)


# --- Specialized / slow ---


@mcp.tool()
@log_tool_duration
def find_method_overrides(
    model_name: str,
    method_name: str,
    max_results: int = 20,
) -> str:
    """SLOW (5–10 s): all Python overrides + super() analysis + load order.

    Use ONLY when you need the full override chain. For a quick code locate, use search_code instead:
        search_code(query='def action_confirm', scope='sale', glob='**/models/*.py')
    """
    return methods_tools.find_method_overrides(_paths, model_name, method_name, max_results=max_results)


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "sse")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
