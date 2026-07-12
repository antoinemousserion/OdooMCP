"""Helpers partagés pour l'analyse de modèles Odoo."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.security import PathSecurityError, resolve_search_path
from odoo_mcp.tools import rg as rg_tools
from odoo_mcp.tools.paths_util import normalize_result_path

_NAME_RE = re.compile(r"""_name\s*=\s*['"]([^'"]+)['"]""")
_INHERIT_SINGLE_RE = re.compile(r"""_inherit\s*=\s*['"]([^'"]+)['"]""")
_INHERIT_LIST_RE = re.compile(r"""_inherit\s*=\s*\[(.*?)\]""", re.DOTALL)
_FIELD_LINE_RE = re.compile(r"""^\s*(\w+)\s*=\s*fields\.(\w+)\(""")
_DEPENDS_RE = re.compile(r"""@api\.depends\((.*?)\)""")


def parse_inherit_values(raw: str) -> list[str]:
    return re.findall(r"""['"]([^'"]+)['"]""", raw)


def module_from_path(paths: OdooPaths, file_path: Path) -> str:
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


def file_relates_to_model(file_path: Path, model_name: str) -> bool:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(
        m in text
        for m in (
            f"_name = '{model_name}'",
            f'_name = "{model_name}"',
            f"_inherit = '{model_name}'",
            f'_inherit = "{model_name}"',
        )
    )


def scan_model_matches(paths: OdooPaths, model_name: str, scope: str = "all") -> tuple[list[dict], list[dict]]:
    from odoo_mcp.tools.odoo import _scan_model_matches

    return _scan_model_matches(paths, model_name, scope)


def files_for_model(paths: OdooPaths, model_name: str, module_hint: str | None = None) -> set[str]:
    from odoo_mcp.tools.odoo import _files_for_model_filter

    return _files_for_model_filter(paths, model_name, module_hint)


def resolve_path_from_relative(paths: OdooPaths, relative: str) -> Path | None:
    from odoo_mcp.security import resolve_safe_path

    try:
        return resolve_safe_path(paths, relative)
    except PathSecurityError:
        return None


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def extract_method_body(lines: list[str], def_line_idx: int) -> tuple[str, int]:
    """Extrait le corps d'une méthode (def_line_idx 0-indexé)."""
    def_line = lines[def_line_idx]
    base_indent = len(def_line) - len(def_line.lstrip())
    body_lines = [def_line]
    for i in range(def_line_idx + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            body_lines.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base_indent and line.strip():
            break
        body_lines.append(line)
    return "\n".join(body_lines), len(body_lines)


def analyze_method_body(body: str, method_name: str) -> dict:
    calls_super = bool(
        re.search(rf"super\s*\([^)]*\)\s*\.\s*{re.escape(method_name)}\s*\(", body)
        or re.search(rf"super\s*\(.+\)\s*\.\s*{re.escape(method_name)}\s*\(", body)
        or re.search(r"super\s*\(\s*\)\s*\.", body)
        or "super()." in body
    )
    self_calls = sorted(set(re.findall(r"self\.(_?\w+)\s*\(", body)))
    private_calls = [c for c in self_calls if c.startswith("_")]
    return {
        "calls_super": calls_super,
        "timing": "wraps_super" if calls_super else "standalone",
        "self_method_calls": self_calls[:10],
        "side_effect_calls": private_calls[:8],
    }


def parse_field_attributes(field_line: str, continuation: str = "") -> dict:
    full = field_line + continuation
    attrs: dict = {}
    for key in ("related", "compute", "inverse", "search"):
        m = re.search(rf"{key}\s*=\s*['\"]([^'\"]+)['\"]", full)
        if m:
            attrs[key] = m.group(1)
        m2 = re.search(rf"{key}\s*=\s*(\w+)", full)
        if m2 and key not in attrs:
            attrs[key] = m2.group(1)
    if re.search(r"\bstore\s*=\s*True", full):
        attrs["store"] = True
    elif re.search(r"\bstore\s*=\s*False", full):
        attrs["store"] = False
    if re.search(r"\btracking\s*=\s*True", full) or re.search(r"\btracking\s*=\s*\d", full):
        attrs["tracking"] = True
    if "compute=" in full or "compute =" in full:
        attrs["is_computed"] = True
    if "related=" in full or "related =" in full:
        attrs["is_related"] = True
    return attrs


def _sanitize_json_value(obj: object) -> object:
    if isinstance(obj, str):
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", obj)
    if isinstance(obj, dict):
        return {k: _sanitize_json_value(v) for k, v in obj.items() if k != "raw"}
    if isinstance(obj, list):
        return [_sanitize_json_value(v) for v in obj]
    return obj


def _trim_payload(obj: object, *, max_list: int = 30, max_str: int = 300) -> object:
    if isinstance(obj, str):
        return obj if len(obj) <= max_str else obj[:max_str] + "..."
    if isinstance(obj, dict):
        return {k: _trim_payload(v, max_list=max_list, max_str=max_str) for k, v in obj.items() if k != "raw"}
    if isinstance(obj, list):
        trimmed = [_trim_payload(v, max_list=max_list, max_str=max_str) for v in obj[:max_list]]
        return trimmed
    return obj


def json_result(payload: dict, *, max_chars: int = 40_000) -> str:
    from odoo_mcp.tools.search import _truncate

    cleaned = _sanitize_json_value(payload)
    text = json.dumps(cleaned, indent=2, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    smaller = _trim_payload(cleaned)
    smaller["_response_truncated"] = True
    text = json.dumps(smaller, indent=2, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return _truncate(text[:max_chars] + "\n}\n")  # fallback — should not happen often


_depends_cache: dict[str, list[str]] | None = None
_depends_cache_key: str | None = None


def _find_manifest_files(paths: OdooPaths) -> list[Path]:
    import shutil
    import subprocess

    search_paths = [p for p in paths.addon_roots if p.is_dir()]
    if not search_paths:
        return []

    if not shutil.which("rg"):
        manifests: list[Path] = []
        for root in search_paths:
            manifests.extend(root.rglob("__manifest__.py"))
        return manifests

    cmd = ["rg", "--files", "-g", "__manifest__.py", "--glob", "!.git/**"]
    cmd.extend(str(p) for p in search_paths)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return []
    if result.returncode not in (0, 1):
        return []
    return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]


def load_module_depends(paths: OdooPaths) -> dict[str, list[str]]:
    global _depends_cache, _depends_cache_key

    cache_key = paths.version
    if _depends_cache is not None and _depends_cache_key == cache_key:
        return _depends_cache

    import logging
    import time

    logger = logging.getLogger("odoo_mcp")
    start = time.perf_counter()
    depends: dict[str, list[str]] = {}
    manifest_files = _find_manifest_files(paths)
    for manifest in manifest_files:
        module = module_from_path(paths, manifest.parent)
        try:
            data = ast.literal_eval(manifest.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                depends[module] = list(data.get("depends", []))
        except (SyntaxError, ValueError):
            depends[module] = []

    _depends_cache = depends
    _depends_cache_key = cache_key
    logger.info(
        "load_module_depends built cache: modules=%d manifests=%d duration_ms=%.1f",
        len(depends),
        len(manifest_files),
        (time.perf_counter() - start) * 1000,
    )
    return depends


def module_load_rank(module: str, depends_map: dict[str, list[str]]) -> int:
    """Rang approximatif de chargement (plus haut = chargé plus tard)."""
    visited: set[str] = set()

    def depth(mod: str) -> int:
        if mod in visited:
            return 0
        visited.add(mod)
        deps = depends_map.get(mod, [])
        if not deps:
            return 0
        return 1 + max((depth(d) for d in deps), default=0)

    return depth(module)

