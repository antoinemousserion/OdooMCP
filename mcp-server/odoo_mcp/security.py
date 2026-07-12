"""Validation des chemins — accès readonly strictement confiné."""

from __future__ import annotations

from pathlib import Path

from odoo_mcp.config import OdooPaths


class PathSecurityError(ValueError):
    pass


def resolve_safe_path(paths: OdooPaths, relative: str) -> Path:
    """Résout un chemin relatif à la racine Odoo, en bloquant les traversées."""
    relative = relative.strip().lstrip("/\\")
    if not relative:
        raise PathSecurityError("Chemin vide.")

    allowed_roots = [paths.community]
    if paths.enterprise:
        allowed_roots.append(paths.enterprise)

    # Essayer chaque racine autorisée
    for root in allowed_roots:
        if not root.is_dir():
            continue
        candidate = (root / relative).resolve()
        root_resolved = root.resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError:
            continue
        if candidate.exists():
            return candidate

    # Chemin absolu explicite sous une racine autorisée
    abs_candidate = Path(relative)
    if abs_candidate.is_absolute():
        for root in allowed_roots:
            if not root.is_dir():
                continue
            try:
                abs_candidate.resolve().relative_to(root.resolve())
                if abs_candidate.exists():
                    return abs_candidate.resolve()
            except ValueError:
                continue

    raise PathSecurityError(
        f"Chemin inaccessible ou inexistant : {relative!r}. "
        f"Racines autorisées : community={paths.community}, enterprise={paths.enterprise}"
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
        # Enterprise : module directement sous la racine
        if paths.enterprise:
            ent_module = paths.enterprise / scope
            if ent_module.is_dir() and (ent_module / "__manifest__.py").exists():
                return [ent_module]

    raise PathSecurityError(
        f"Scope inconnu : {scope!r}. Utilisez 'all', 'community', 'enterprise' ou un nom de module."
    )
