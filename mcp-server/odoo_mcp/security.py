"""Validation des chemins — accès readonly strictement confiné."""

from __future__ import annotations

from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.tools.paths_util import normalize_input_path


class PathSecurityError(ValueError):
    pass


def resolve_safe_path(paths: OdooPaths, relative: str) -> Path:
    """Résout un chemin relatif — accepte community/, workspace_hint, addons/..."""
    relative = relative.strip()
    if not relative:
        raise PathSecurityError("Chemin vide.")

    allowed_roots = [paths.community]
    if paths.enterprise:
        allowed_roots.append(paths.enterprise)

    candidates = normalize_input_path(paths, relative)
    if not candidates:
        raise PathSecurityError("Chemin vide.")

    for candidate in candidates:
        for root in allowed_roots:
            if not root.is_dir():
                continue
            resolved = (root / candidate).resolve()
            root_resolved = root.resolve()
            try:
                resolved.relative_to(root_resolved)
            except ValueError:
                continue
            if resolved.exists():
                return resolved

    # Chemin absolu explicite sous une racine autorisée
    abs_candidate = Path(relative.replace("\\", "/"))
    if abs_candidate.is_absolute():
        for root in allowed_roots:
            if not root.is_dir():
                continue
            try:
                resolved = abs_candidate.resolve()
                resolved.relative_to(root.resolve())
                if resolved.exists():
                    return resolved
            except ValueError:
                continue

    raise PathSecurityError(
        f"Chemin inaccessible ou inexistant : {relative!r}. "
        f"Formats acceptés : addons/sale/..., community/addons/sale/..., "
        f"odoo_v{paths.version}/addons/sale/..."
    )


def resolve_search_path(paths: OdooPaths, scope: str | None) -> list[Path]:
    """Détermine les répertoires de recherche selon le scope."""
    if not scope or scope == "all":
        return [p for p in paths.search_roots if p.is_dir()]

    scope = scope.strip().lower()
    if scope == "community":
        return [paths.community] if paths.community.is_dir() else []
    if scope == "enterprise":
        if paths.enterprise and paths.enterprise.is_dir():
            return [paths.enterprise]
        return []

    # Scope = nom de module addon
    for addon_root in paths.addon_roots:
        module_path = addon_root / scope
        if module_path.is_dir() and (module_path / "__manifest__.py").exists():
            return [module_path]
        if paths.enterprise:
            ent_module = paths.enterprise / scope
            if ent_module.is_dir() and (ent_module / "__manifest__.py").exists():
                return [ent_module]

    raise PathSecurityError(
        f"Scope inconnu : {scope!r}. Utilisez 'all', 'community', 'enterprise' ou un nom de module."
    )
