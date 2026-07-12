"""Outils spécialisés pour la structure Odoo (modules, modèles, champs)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.tools.search import MAX_OUTPUT_CHARS, _truncate, search_code


def list_addons(paths: OdooPaths, *, edition: str = "all") -> str:
    """Liste les modules Odoo détectés via __manifest__.py / __openerp__.py."""
    modules: list[dict[str, str]] = []

    def scan(root: Path, label: str) -> None:
        if not root.is_dir():
            return
        for manifest in root.rglob("__manifest__.py"):
            module_dir = manifest.parent
            try:
                rel = module_dir.relative_to(root)
                name = rel.parts[0] if len(rel.parts) > 1 else module_dir.name
            except ValueError:
                name = module_dir.name
            modules.append({"name": name, "edition": label, "path": str(module_dir)})

    if edition in ("all", "community") and paths.community.is_dir():
        for addon_root in (paths.community / "addons", paths.community / "odoo" / "addons"):
            if addon_root.is_dir():
                scan(addon_root, "community")
    if edition in ("all", "enterprise") and paths.enterprise and paths.enterprise.is_dir():
        scan(paths.enterprise, "enterprise")

    if not modules:
        return "Aucun module détecté. Le clone Git est peut-être en cours ou absent."

    # Dédupliquer par nom (enterprise prioritaire si doublon)
    by_name: dict[str, dict[str, str]] = {}
    for mod in sorted(modules, key=lambda m: (m["edition"] != "enterprise", m["name"])):
        by_name[mod["name"]] = mod

    lines = [f"{m['name']} ({m['edition']}) — {m['path']}" for m in sorted(by_name.values(), key=lambda m: m["name"])]
    return _truncate(f"{len(lines)} module(s)\n\n" + "\n".join(lines))


def find_model(paths: OdooPaths, model_name: str, *, max_results: int = 30) -> str:
    """Recherche la définition d'un modèle Odoo (_name = '...')."""
    escaped = model_name.replace("'", "\\'")
    patterns = [
        rf"_name\s*=\s*['\"]{escaped}['\"]",
        rf"_inherit\s*=\s*['\"]{escaped}['\"]",
        rf"_inherit\s*=\s*\[[^\]]*['\"]{escaped}['\"]",
    ]
    results: list[str] = []
    for pattern in patterns:
        chunk = search_code(paths, pattern, scope="all", max_results=max_results)
        if "Aucun résultat" not in chunk:
            results.append(chunk)
    if not results:
        return f"Aucune définition trouvée pour le modèle {model_name!r}."
    return _truncate("\n\n---\n\n".join(results))


def find_field(paths: OdooPaths, field_name: str, *, model_hint: str | None = None) -> str:
    """Recherche un champ Odoo (fields.XXX('field_name'))."""
    pattern = rf"fields\.\w+\(\s*['\"]{field_name}['\"]"
    scope = model_hint if model_hint else "all"
    return search_code(paths, pattern, scope=scope, max_results=50)


def get_module_info(paths: OdooPaths, module_name: str) -> str:
    """Retourne le manifest et la structure d'un module."""
    for addon_root in paths.addon_roots:
        module_path = addon_root / module_name
        manifest = module_path / "__manifest__.py"
        if manifest.is_file():
            break
    else:
        if paths.enterprise:
            module_path = paths.enterprise / module_name
            manifest = module_path / "__manifest__.py"
        else:
            module_path = Path()
            manifest = Path()

    if not manifest.is_file():
        return f"Module {module_name!r} introuvable."

    manifest_text = manifest.read_text(encoding="utf-8", errors="replace")
    try:
        manifest_data = ast.literal_eval(manifest_text)
    except (SyntaxError, ValueError):
        manifest_data = {"_raw": manifest_text[:2000]}

    structure: list[str] = []
    for entry in sorted(module_path.iterdir()):
        if entry.is_dir():
            py_count = len(list(entry.glob("*.py")))
            structure.append(f"  {entry.name}/ ({py_count} fichiers .py)")
        elif entry.suffix == ".py":
            structure.append(f"  {entry.name}")

    info = {
        "module": module_name,
        "path": str(module_path),
        "manifest": manifest_data,
        "structure": structure[:40],
    }
    return _truncate(json.dumps(info, indent=2, ensure_ascii=False))


def get_codebase_status(paths: OdooPaths) -> str:
    """État des repos community/enterprise montés."""
    status: dict[str, object] = {
        "version": paths.version,
        "community": _repo_status(paths.community),
        "enterprise": _repo_status(paths.enterprise) if paths.enterprise else {"present": False},
        "addon_roots": [str(p) for p in paths.addon_roots],
    }
    return json.dumps(status, indent=2, ensure_ascii=False)


def _repo_status(path: Path | None) -> dict[str, object]:
    if not path or not path.is_dir():
        return {"present": False}
    git_dir = path / ".git"
    info: dict[str, object] = {"present": True, "path": str(path)}
    if git_dir.is_dir():
        head = (path / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        info["git_head"] = head
        try:
            py_files = sum(1 for _ in path.rglob("*.py"))
            info["python_files"] = py_files
        except OSError:
            pass
    return info
