"""Configuration du serveur MCP Odoo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OdooPaths:
    version: str
    root: Path
    community: Path
    enterprise: Path | None

    @property
    def search_roots(self) -> list[Path]:
        roots = [self.community]
        if self.enterprise and self.enterprise.is_dir():
            roots.append(self.enterprise)
        return roots

    @property
    def addon_roots(self) -> list[Path]:
        """Chemins typiques des addons Odoo (community + enterprise)."""
        candidates: list[Path] = []
        for root in self.search_roots:
            for sub in ("addons", "odoo/addons"):
                path = root / sub
                if path.is_dir():
                    candidates.append(path)
            # Enterprise : les modules sont à la racine du repo
            if root == self.enterprise:
                candidates.append(root)
        # Dédupliquer en conservant l'ordre
        seen: set[Path] = set()
        unique: list[Path] = []
        for path in candidates:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                unique.append(path)
        return unique


def get_odoo_paths() -> OdooPaths:
    version = os.environ.get("ODOO_VERSION", "18")
    root = Path(os.environ.get("ODOO_DATA_DIR", f"/data/odoo/v{version}"))
    community = Path(os.environ.get("ODOO_COMMUNITY_DIR", root / "community"))
    enterprise_dir = os.environ.get("ODOO_ENTERPRISE_DIR")
    enterprise = Path(enterprise_dir) if enterprise_dir else root / "enterprise"
    if not enterprise.is_dir():
        enterprise = None  # type: ignore[assignment]
    return OdooPaths(version=version, root=root, community=community, enterprise=enterprise)
