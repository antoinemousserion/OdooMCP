"""Outils spécialisés Odoo : modèles, champs, méthodes, modules."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.security import PathSecurityError, resolve_search_path
from odoo_mcp.tools import rg as rg_tools
from odoo_mcp.tools.model_utils import parse_field_attributes, read_lines
from odoo_mcp.tools.paths_util import enterprise_warning, normalize_result_path, resolve_scope
from odoo_mcp.tools.search import MAX_OUTPUT_CHARS, _truncate

_NAME_RE = re.compile(r"""_name\s*=\s*['"]([^'"]+)['"]""")
_INHERIT_SINGLE_RE = re.compile(r"""_inherit\s*=\s*['"]([^'"]+)['"]""")
_INHERIT_LIST_RE = re.compile(r"""_inherit\s*=\s*\[(.*?)\]""", re.DOTALL)
def _field_definition_pattern(field_name: str) -> str:
    return rf"^\s*{re.escape(field_name)}\s*=\s*fields\.\w+"


def _is_field_assignment(line: str, field_name: str) -> bool:
    return bool(re.match(rf"^\s*\w+\.{re.escape(field_name)}\s*=", line))


def _file_relates_to_model(file_path: Path, model_name: str) -> bool:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(
        marker in text
        for marker in (
            f"_name = '{model_name}'",
            f'_name = "{model_name}"',
            f"_inherit = '{model_name}'",
            f'_inherit = "{model_name}"',
        )
    )


def _iter_model_files(paths: OdooPaths, scope: str = "all") -> list[Path]:
    try:
        search_roots = resolve_search_path(paths, scope)
    except PathSecurityError:
        return []
    files: list[Path] = []
    for root in search_roots:
        if not root.is_dir():
            continue
        files.extend(root.rglob("models/*.py"))
        files.extend(root.rglob("*models/*.py"))
    return sorted(set(files))


def _module_from_path(paths: OdooPaths, file_path: Path) -> str:
    info = normalize_result_path(file_path, paths)
    rel = info["relative_path"]
    parts = rel.split("/")
    for key in ("addons", "enterprise"):
        if key in parts:
            idx = parts.index(key)
            if idx + 1 < len(parts):
                return parts[idx + 1]
    for part in parts:
        if part not in ("community", "enterprise", "odoo", "addons", "models") and not part.endswith(".py"):
            return part
    return "unknown"


def _parse_inherit_values(raw: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"""['"]([^'"]+)['"]""", raw):
        values.append(match.group(1))
    return values


def _scan_model_matches(paths: OdooPaths, model_name: str, scope: str = "all") -> tuple[list[dict], list[dict]]:
    definitions: list[dict] = []
    inherits: list[dict] = []
    seen_def: set[tuple[str, int]] = set()
    seen_inh: set[tuple[str, int]] = set()

    try:
        search_paths = resolve_search_path(paths, scope)
    except PathSecurityError:
        return definitions, inherits

    # Ripgrep fixed-string : rapide sur toute la codebase
    rg_patterns: list[tuple[str, str]] = [
        (f"_name = '{model_name}'", "_name"),
        (f'_name = "{model_name}"', "_name"),
        (f"_inherit = '{model_name}'", "_inherit"),
        (f'_inherit = "{model_name}"', "_inherit"),
    ]

    for fixed, match_type in rg_patterns:
        matches, _ = rg_tools.run_rg(
            paths,
            fixed,
            search_paths,
            scope=scope,
            glob="**/*.py",
            file_type="py",
            max_results=200,
            fixed_string=True,
        )
        for m in matches:
            key = (m.relative_path, m.line)
            entry = {
                "file": m.relative_path,
                "workspace_hint": m.workspace_hint,
                "line": m.line,
                "type": match_type,
                "module": _module_from_path(paths, Path(m.file)),
                "content": m.content.strip(),
            }
            if match_type == "_name" and key not in seen_def:
                seen_def.add(key)
                definitions.append(entry)
            elif match_type == "_inherit" and key not in seen_inh:
                seen_inh.add(key)
                inherits.append(entry)

    # _inherit = ['a', 'b', model] — regex ripgrep sur une seule ligne
    list_patterns = [
        rf"_inherit\s*=\s*\[[^\]]*['\"]{re.escape(model_name)}['\"]",
    ]
    for pattern in list_patterns:
        matches, _ = rg_tools.run_rg(
            paths,
            pattern,
            search_paths,
            scope=scope,
            glob="**/*.py",
            file_type="py",
            max_results=200,
        )
        for m in matches:
            key = (m.relative_path, m.line)
            if key in seen_inh:
                continue
            seen_inh.add(key)
            inherits.append(
                {
                    "file": m.relative_path,
                    "workspace_hint": m.workspace_hint,
                    "line": m.line,
                    "type": "_inherit",
                    "module": _module_from_path(paths, Path(m.file)),
                    "content": m.content.strip(),
                }
            )

    return definitions, inherits


def find_model(
    paths: OdooPaths,
    model_name: str | None = None,
    *,
    model: str | None = None,
    max_results: int = 30,
) -> str:
    effective_name = (model_name or model or "").strip()
    if not effective_name:
        return "Error: provide 'model_name' or 'model'."

    definitions, inherits = _scan_model_matches(paths, effective_name)

    if not definitions and not inherits:
        warning = enterprise_warning(paths, "all")
        payload: dict = {
            "model": effective_name,
            "definitions": [],
            "inherits": [],
            "main_file": None,
            "message": f"No definition found for model {effective_name!r}.",
        }
        if warning:
            payload["warning"] = warning
        return _truncate(json.dumps(payload, indent=2, ensure_ascii=False))

    main_file = definitions[0]["file"] if definitions else inherits[0]["file"]
    payload = {
        "model": effective_name,
        "definitions": definitions[:max_results],
        "inherits": inherits[:max_results],
        "definitions_total": len(definitions),
        "definitions_truncated": len(definitions) > max_results,
        "inherits_total": len(inherits),
        "inherits_truncated": len(inherits) > max_results,
        "main_file": main_file,
        "main_file_workspace_hint": definitions[0]["workspace_hint"] if definitions else inherits[0]["workspace_hint"],
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return _truncate(json.dumps(payload, indent=2, ensure_ascii=False))


def _files_for_model_filter(
    paths: OdooPaths,
    model_name: str,
    module_hint: str | None = None,
) -> set[str]:
    """Fichiers models pertinents pour un modèle (ex. sale.order → sale_order.py, sale_order_line.py)."""
    definitions, inherits = _scan_model_matches(paths, model_name)
    files = {item["file"] for item in definitions + inherits}

    module = (module_hint or model_name.split(".")[0]).strip()
    stem = model_name.replace(".", "_")

    try:
        search_paths = resolve_search_path(paths, module)
    except PathSecurityError:
        search_paths = [p for p in paths.search_roots if p.is_dir()]

    for root in search_paths:
        if not root.is_dir():
            continue
        for py_file in root.rglob("models/*.py"):
            name = py_file.stem
            if name == stem or name.startswith(f"{stem}_") or (stem.startswith(name) and name.endswith("_line")):
                info = normalize_result_path(py_file, paths)
                files.add(info["relative_path"])
            elif _file_relates_to_model(py_file, model_name):
                info = normalize_result_path(py_file, paths)
                files.add(info["relative_path"])

    return files


def find_field(
    paths: OdooPaths,
    field_name: str,
    *,
    module_hint: str | None = None,
    model_name: str | None = None,
) -> str:
    if model_name:
        scope = module_hint or model_name.split(".")[0]
        allowed_files = _files_for_model_filter(paths, model_name, module_hint)
    else:
        scope = resolve_scope("all", module_hint)
        allowed_files = None

    try:
        search_paths = resolve_search_path(paths, scope)
    except PathSecurityError as exc:
        return f"Error: {exc}"

    field_pattern = _field_definition_pattern(field_name)
    field_def_re = re.compile(rf"^\s*{re.escape(field_name)}\s*=\s*fields\.(\w+)")

    matches, _ = rg_tools.run_rg(
        paths,
        field_pattern,
        search_paths,
        scope=scope,
        glob="**/models/*.py",
        file_type="py",
        max_results=300 if allowed_files else 200,
        fixed_string=False,
    )

    results: list[dict] = []
    seen: set[tuple[str, int]] = set()

    for m in matches:
        if allowed_files is not None and m.relative_path not in allowed_files:
            continue
        line = m.content.strip()
        if _is_field_assignment(line, field_name):
            continue
        fm = field_def_re.match(line)
        if not fm:
            continue
        key = (m.relative_path, m.line)
        if key in seen:
            continue
        seen.add(key)
        file_path = Path(m.file)
        attrs = parse_field_attributes(line)
        if line.count("(") > line.count(")") and file_path.is_file():
            extra_lines = read_lines(file_path)
            for j in range(m.line, min(m.line + 8, len(extra_lines))):
                attrs = parse_field_attributes(line, " ".join(extra_lines[m.line : j]))
        results.append(
            {
                "file": m.relative_path,
                "workspace_hint": m.workspace_hint,
                "line": m.line,
                "module": _module_from_path(paths, file_path),
                "field_type": fm.group(1),
                "content": line,
                "model_file": file_path.name,
                **{k: v for k, v in attrs.items() if k != "raw"},
            }
        )

    warning = enterprise_warning(paths, scope)
    payload: dict = {
        "field": field_name,
        "scope": scope,
        "module_hint": module_hint,
        "model_name": model_name,
        "model_files_filter": sorted(allowed_files) if allowed_files else None,
        "results": results[:50],
        "results_total": len(results),
        "results_truncated": len(results) > 50,
    }
    if warning:
        payload["warning"] = warning
    if not results:
        hint = ""
        if model_name:
            hint = f" Try model_name={model_name!r} with a narrower module_hint."
        payload["message"] = f"No field definition found for {field_name!r}.{hint}"
    return _truncate(json.dumps(payload, indent=2, ensure_ascii=False))


def get_model_inheritance_chain(paths: OdooPaths, model_name: str) -> str:
    """Chaîne d'extensions _inherit pour un modèle (BFS par fichiers/modules)."""
    definitions, direct_inherits = _scan_model_matches(paths, model_name)

    chain: list[dict] = [
        {
            "model": model_name,
            "depth": 0,
            "role": "base",
            "files": definitions,
        }
    ]

    visited_files: set[str] = {d["file"] for d in definitions}
    queue: list[tuple[dict, int]] = [(entry, 1) for entry in direct_inherits]

    while queue:
        entry, depth = queue.pop(0)
        if entry["file"] in visited_files:
            continue
        visited_files.add(entry["file"])
        chain.append(
            {
                "model": model_name,
                "depth": depth,
                "role": "extension",
                **entry,
            }
        )

    payload = {
        "model": model_name,
        "chain": chain,
        "direct_extensions": direct_inherits,
        "extensions_total": len(direct_inherits),
        "base_definitions": definitions,
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return _truncate(json.dumps(payload, indent=2, ensure_ascii=False))


def find_method_overrides(
    paths: OdooPaths,
    model_name: str,
    method_name: str,
    *,
    max_results: int = 50,
) -> str:
    from odoo_mcp.tools import methods as methods_tools

    return methods_tools.find_method_overrides(
        paths, model_name, method_name, max_results=max_results
    )


def find_xml_action(
    paths: OdooPaths,
    action_name: str,
    *,
    model: str | None = None,
    max_results: int = 30,
) -> str:
    patterns = [
        rf'name="{re.escape(action_name)}"',
        rf"name='{re.escape(action_name)}'",
    ]
    results: list[dict] = []
    seen: set[tuple[str, int]] = set()

    for pattern in patterns:
        matches, _ = rg_tools.run_rg(
            paths,
            pattern,
            [p for p in paths.search_roots if p.is_dir()],
            scope="all",
            glob="**/*.xml",
            file_type=None,
            max_results=max_results * 2,
            fixed_string=True,
        )
        for m in matches:
            if model and model not in m.content and action_name not in m.content:
                continue
            key = (m.relative_path, m.line)
            if key in seen:
                continue
            seen.add(key)
            action_type = "unknown"
            if "type=\"object\"" in m.content or "type='object'" in m.content:
                action_type = "object"
            elif "type=\"action\"" in m.content or "type='action'" in m.content:
                action_type = "action"
            results.append(
                {
                    "file": m.relative_path,
                    "workspace_hint": m.workspace_hint,
                    "line": m.line,
                    "content": m.content.strip(),
                    "action_type": action_type,
                }
            )

    payload = {
        "action": action_name,
        "model_filter": model,
        "results": results[:max_results],
        "results_total": len(results),
        "results_truncated": len(results) > max_results,
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return _truncate(json.dumps(payload, indent=2, ensure_ascii=False))


def find_references(
    paths: OdooPaths,
    model: str,
    symbol: str,
    *,
    max_results: int = 50,
) -> str:
    patterns = [
        symbol,
        f"{model}.{symbol}" if "." not in symbol else symbol,
        f"'{symbol}'",
        f'"{symbol}"',
    ]
    seen: set[tuple[str, int]] = set()
    results: list[dict] = []

    for pattern in patterns:
        matches, _ = rg_tools.run_rg(
            paths,
            pattern,
            [p for p in paths.search_roots if p.is_dir()],
            scope="all",
            file_type=None,
            max_results=max_results,
            fixed_string=True,
        )
        for m in matches:
            key = (m.relative_path, m.line)
            if key in seen:
                continue
            seen.add(key)
            if model.replace(".", "_") not in m.relative_path and model.split(".")[0] not in m.content and model not in m.content:
                if symbol not in m.content:
                    continue
            results.append(
                {
                    "file": m.relative_path,
                    "workspace_hint": m.workspace_hint,
                    "line": m.line,
                    "content": m.content,
                }
            )

    payload = {"model": model, "symbol": symbol, "references": results[:max_results]}
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return _truncate(json.dumps(payload, indent=2, ensure_ascii=False))


def get_module_dependencies(paths: OdooPaths, module_name: str) -> str:
    manifest_path: Path | None = None
    module_path: Path | None = None

    for addon_root in paths.addon_roots:
        candidate = addon_root / module_name / "__manifest__.py"
        if candidate.is_file():
            manifest_path = candidate
            module_path = candidate.parent
            break
    if not manifest_path and paths.enterprise:
        candidate = paths.enterprise / module_name / "__manifest__.py"
        if candidate.is_file():
            manifest_path = candidate
            module_path = candidate.parent

    if not manifest_path or not module_path:
        return json.dumps({"module": module_name, "error": "Module not found."}, indent=2)

    manifest_text = manifest_path.read_text(encoding="utf-8", errors="replace")
    try:
        manifest_data = ast.literal_eval(manifest_text)
    except (SyntaxError, ValueError):
        manifest_data = {"_raw": manifest_text[:2000]}

    depends = manifest_data.get("depends", []) if isinstance(manifest_data, dict) else []
    info = normalize_result_path(module_path, paths)

    payload = {
        "module": module_name,
        "path": info["relative_path"],
        "workspace_hint": info["workspace_hint"],
        "depends": depends,
        "manifest": {k: manifest_data[k] for k in ("name", "version", "depends", "auto_install") if k in manifest_data}
        if isinstance(manifest_data, dict)
        else manifest_data,
    }
    return _truncate(json.dumps(payload, indent=2, ensure_ascii=False))


def list_addons(paths: OdooPaths, *, edition: str = "all") -> str:
    modules: list[dict[str, str]] = []

    def scan(root: Path, label: str) -> None:
        if not root.is_dir():
            return
        for manifest in root.rglob("__manifest__.py"):
            module_dir = manifest.parent
            info = normalize_result_path(module_dir, paths)
            try:
                rel = module_dir.relative_to(root)
                name = rel.parts[0] if len(rel.parts) > 1 else module_dir.name
            except ValueError:
                name = module_dir.name
            modules.append(
                {
                    "name": name,
                    "edition": label,
                    "path": info["relative_path"],
                    "workspace_hint": info["workspace_hint"],
                }
            )

    if edition in ("all", "community") and paths.community.is_dir():
        for addon_root in (paths.community / "addons", paths.community / "odoo" / "addons"):
            if addon_root.is_dir():
                scan(addon_root, "community")
    if edition in ("all", "enterprise") and paths.enterprise and paths.enterprise.is_dir():
        scan(paths.enterprise, "enterprise")

    if not modules:
        return "No modules detected."

    by_name: dict[str, dict[str, str]] = {}
    for mod in sorted(modules, key=lambda m: (m["edition"] != "enterprise", m["name"])):
        by_name[mod["name"]] = mod

    lines = [
        f"{m['name']} ({m['edition']}) — {m['path']}  [workspace: {m['workspace_hint']}]"
        for m in sorted(by_name.values(), key=lambda m: m["name"])
    ]
    return _truncate(f"{len(lines)} module(s)\n\n" + "\n".join(lines))


def get_module_info(paths: OdooPaths, module_name: str) -> str:
    module_path: Path | None = None
    manifest: Path | None = None

    for addon_root in paths.addon_roots:
        candidate = addon_root / module_name
        manifest_file = candidate / "__manifest__.py"
        if manifest_file.is_file():
            module_path = candidate
            manifest = manifest_file
            break
    if not manifest and paths.enterprise:
        candidate = paths.enterprise / module_name
        manifest_file = candidate / "__manifest__.py"
        if manifest_file.is_file():
            module_path = candidate
            manifest = manifest_file

    if not manifest or not module_path:
        return f"Module {module_name!r} not found."

    manifest_text = manifest.read_text(encoding="utf-8", errors="replace")
    try:
        manifest_data = ast.literal_eval(manifest_text)
    except (SyntaxError, ValueError):
        manifest_data = {"_raw": manifest_text[:2000]}

    structure: list[str] = []
    for entry in sorted(module_path.iterdir()):
        if entry.is_dir():
            py_count = len(list(entry.glob("*.py")))
            structure.append(f"  {entry.name}/ ({py_count} .py files)")
        elif entry.suffix == ".py":
            structure.append(f"  {entry.name}")

    info = normalize_result_path(module_path, paths)
    payload = {
        "module": module_name,
        "path": info["relative_path"],
        "workspace_hint": info["workspace_hint"],
        "manifest": manifest_data,
        "structure": structure[:40],
    }
    return _truncate(json.dumps(payload, indent=2, ensure_ascii=False))


def get_codebase_status(paths: OdooPaths) -> str:
    status: dict[str, object] = {
        "version": paths.version,
        "community": _repo_status(paths.community),
        "enterprise": _repo_status(paths.enterprise) if paths.enterprise else {"present": False},
        "enterprise_available": paths.enterprise is not None and paths.enterprise.is_dir(),
        "addon_roots": [str(p) for p in paths.addon_roots],
        "workspace_prefix_hint": f"odoo_v{paths.version}",
    }
    if not status["enterprise_available"]:
        status["warning"] = (
            "Enterprise not mounted — enterprise modules are not searchable. "
            "Configure GITHUB_TOKEN + CLONE_ENTERPRISE=true."
        )
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
