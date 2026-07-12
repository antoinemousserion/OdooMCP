"""Serveur MCP readonly pour explorer une codebase Odoo."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from odoo_mcp.config import get_odoo_paths
from odoo_mcp.security import PathSecurityError
from odoo_mcp.tools import odoo as odoo_tools
from odoo_mcp.tools import search as search_tools

_paths = get_odoo_paths()
_version = _paths.version

mcp = FastMCP(
    name=f"Odoo {_version}",
    instructions=(
        f"Serveur MCP readonly pour Odoo {_version}. "
        "Codebases montées : community + enterprise (si disponible). "
        "Utilisez search_code pour du grep rapide, find_model pour les modèles Odoo, "
        "list_addons pour lister les modules, et read_file pour lire du code. "
        "Scope : 'all', 'community', 'enterprise', ou nom de module (ex. 'sale')."
    ),
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8000")),
)


@mcp.tool()
def get_codebase_status() -> str:
    """État du clone Git Odoo (community/enterprise, branche, nombre de fichiers)."""
    return odoo_tools.get_codebase_status(_paths)


@mcp.tool()
def search_code(
    pattern: str,
    scope: str = "all",
    glob_pattern: str | None = None,
    file_type: str | None = "py",
    context_lines: int = 0,
    max_results: int = 100,
    case_insensitive: bool = False,
) -> str:
    """Recherche regex dans le code Odoo via ripgrep (optimisé grosses codebases).

    Args:
        pattern: Expression régulière (ripgrep).
        scope: 'all', 'community', 'enterprise', ou nom de module (ex. 'account').
        glob_pattern: Filtre glob optionnel (ex. '**/models/*.py').
        file_type: Type ripgrep (py, xml, js…). None pour tous les fichiers.
        context_lines: Lignes de contexte autour de chaque match.
        max_results: Nombre max de résultats.
        case_insensitive: Recherche insensible à la casse.
    """
    return search_tools.search_code(
        _paths,
        pattern,
        scope=scope,
        glob=glob_pattern,
        file_type=file_type,
        context=context_lines,
        max_results=max_results,
        case_insensitive=case_insensitive,
    )


@mcp.tool()
def glob_files(
    pattern: str,
    scope: str = "all",
    max_results: int = 100,
) -> str:
    """Trouve des fichiers par pattern glob (ex. '**/sale_order*.py').

    Args:
        pattern: Pattern glob (ex. '*.xml', '**/views/*.xml').
        scope: 'all', 'community', 'enterprise', ou nom de module.
        max_results: Nombre max de fichiers retournés.
    """
    return search_tools.glob_files(_paths, pattern, scope=scope, max_results=max_results)


@mcp.tool()
def read_file(
    path: str,
    offset: int = 1,
    limit: int = 200,
) -> str:
    """Lit un fichier source Odoo (readonly, avec numéros de lignes).

    Args:
        path: Chemin relatif depuis community/ ou enterprise/ (ex. 'addons/sale/models/sale_order.py').
        offset: Numéro de ligne de départ (1-indexé).
        limit: Nombre max de lignes à retourner.
    """
    try:
        return search_tools.read_file(_paths, path, offset=offset, limit=limit)
    except PathSecurityError as exc:
        return f"Erreur : {exc}"


@mcp.tool()
def list_directory(
    path: str = "",
    max_entries: int = 200,
) -> str:
    """Liste le contenu d'un répertoire dans la codebase Odoo.

    Args:
        path: Chemin relatif (vide = racines community/enterprise).
        max_entries: Nombre max d'entrées.
    """
    try:
        return search_tools.list_directory(_paths, path, max_entries=max_entries)
    except PathSecurityError as exc:
        return f"Erreur : {exc}"


@mcp.tool()
def list_addons(edition: str = "all") -> str:
    """Liste tous les modules Odoo détectés (via __manifest__.py).

    Args:
        edition: 'all', 'community', ou 'enterprise'.
    """
    return odoo_tools.list_addons(_paths, edition=edition)


@mcp.tool()
def find_model(model_name: str, max_results: int = 30) -> str:
    """Trouve où un modèle Odoo est défini ou hérité (_name / _inherit).

    Args:
        model_name: Nom technique du modèle (ex. 'sale.order').
        max_results: Nombre max de résultats par type de match.
    """
    return odoo_tools.find_model(_paths, model_name, max_results=max_results)


@mcp.tool()
def find_field(field_name: str, model_hint: str | None = None) -> str:
    """Trouve les définitions d'un champ Odoo (fields.Xxx('nom')).

    Args:
        field_name: Nom du champ (ex. 'amount_total').
        model_hint: Nom de module pour limiter la recherche (ex. 'sale').
    """
    return odoo_tools.find_field(_paths, field_name, model_hint=model_hint)


@mcp.tool()
def get_module_info(module_name: str) -> str:
    """Retourne le __manifest__.py et la structure d'un module Odoo.

    Args:
        module_name: Nom du module (ex. 'sale', 'account_accountant').
    """
    return odoo_tools.get_module_info(_paths, module_name)


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "sse")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
