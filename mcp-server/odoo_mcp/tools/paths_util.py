"""Utilitaires chemins, warnings et formatage des résultats."""

from __future__ import annotations

import os
from pathlib import Path

from odoo_mcp.config import OdooPaths


def enterprise_available(paths: OdooPaths) -> bool:
    return paths.enterprise is not None and paths.enterprise.is_dir()


def enterprise_warning(paths: OdooPaths, scope: str) -> str | None:
    if scope in ("enterprise", "community"):
        return None
    if enterprise_available(paths):
        return None
    return (
        "WARNING: enterprise codebase not mounted — enterprise-only modules "
        "(sale_subscription, sale_planning, account_accountant, …) are not searchable. "
        "Set GITHUB_TOKEN and CLONE_ENTERPRISE=true, then restart the container."
    )


def normalize_result_path(abs_path: str | Path, paths: OdooPaths) -> dict[str, str]:
    """Convertit un chemin absolu Docker en chemins relatifs exploitables."""
    path = Path(abs_path).resolve()
    edition = "unknown"
    relative = str(path)

    for label, root in (
        ("community", paths.community),
        ("enterprise", paths.enterprise),
    ):
        if not root or not root.is_dir():
            continue
        try:
            rel = path.relative_to(root.resolve())
            edition = label
            relative = rel.as_posix()
            break
        except ValueError:
            continue

    prefix = os.environ.get("ODOO_WORKSPACE_PREFIX", f"odoo_v{paths.version}")
    workspace_hint = _to_workspace_hint(relative, edition, prefix)

    return {
        "file": str(path),
        "relative_path": f"{edition}/{relative}" if edition != "unknown" else relative,
        "edition": edition,
        "workspace_hint": workspace_hint,
    }


def _to_workspace_hint(relative: str, edition: str, prefix: str) -> str:
    """Mappe vers la convention workspace locale typique (odoo_vXX/...)."""
    rel = relative.replace("\\", "/")
    if edition == "enterprise":
        return f"{prefix}/enterprise/{rel}"
    if rel.startswith("odoo/addons/"):
        return f"{prefix}/odoo/{rel}"
    if rel.startswith("addons/"):
        return f"{prefix}/{rel}"
    return f"{prefix}/{rel}"


def normalize_input_path(paths: OdooPaths, path: str) -> list[str]:
    """Normalise et génère des candidats pour résoudre un chemin utilisateur."""
    raw = path.strip().replace("\\", "/").lstrip("/")
    if not raw:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        candidate = candidate.lstrip("/")
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    add(raw)

    prefix = os.environ.get("ODOO_WORKSPACE_PREFIX", f"odoo_v{paths.version}")
    if raw.startswith(f"{prefix}/"):
        add(raw[len(prefix) + 1 :])

    for edition in ("community", "enterprise"):
        if raw.startswith(f"{edition}/"):
            add(raw[len(edition) + 1 :])

    if raw.startswith("odoo/addons/"):
        add(raw[len("odoo/") :])

    if raw.startswith("addons/"):
        add(f"odoo/{raw}")

    return candidates


def resolve_scope(scope: str | None, module_hint: str | None = None) -> str:
    """Normalise scope : module_hint prioritaire, 'sale.order' -> module 'sale'."""
    if module_hint:
        hint = module_hint.strip()
        if "." in hint and not hint.startswith("."):
            return hint.split(".", 1)[0]
        return hint
    return scope or "all"
