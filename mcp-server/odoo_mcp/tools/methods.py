"""Outils méthodes : overrides, chaîne d'appels, callers."""

from __future__ import annotations

import json
import re
from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.tools import rg as rg_tools
from odoo_mcp.tools.model_utils import (
    analyze_method_body,
    extract_method_body,
    file_relates_to_model,
    json_result,
    load_module_depends,
    module_from_path,
    module_load_rank,
    read_lines,
    resolve_path_from_relative,
    scan_model_matches,
)
from odoo_mcp.tools.paths_util import enterprise_warning


def find_method_overrides(
    paths: OdooPaths,
    model_name: str,
    method_name: str,
    *,
    max_results: int = 50,
    analyze_body: bool = True,
) -> str:
    definitions, inherits = scan_model_matches(paths, model_name)
    related_files = {item["file"] for item in definitions + inherits}
    base_files = {d["file"] for d in definitions}

    method_pattern = rf"^\s*def\s+{re.escape(method_name)}\s*\("
    search_paths = [p for p in paths.search_roots if p.is_dir()]

    matches, _ = rg_tools.run_rg(
        paths,
        method_pattern,
        search_paths,
        scope="all",
        glob="**/models/*.py",
        file_type="py",
        max_results=500,
    )

    depends_map = load_module_depends(paths)
    results: list[dict] = []
    seen: set[tuple[str, int]] = set()

    for m in matches:
        if not (m.relative_path in related_files or file_relates_to_model(Path(m.file), model_name)):
            continue
        key = (m.relative_path, m.line)
        if key in seen:
            continue
        seen.add(key)

        entry: dict = {
            "file": m.relative_path,
            "workspace_hint": m.workspace_hint,
            "line": m.line,
            "module": module_from_path(paths, Path(m.file)),
            "content": m.content.strip(),
            "kind": "base" if m.relative_path in base_files else "override",
            "load_rank": module_load_rank(module_from_path(paths, Path(m.file)), depends_map),
        }

        if analyze_body:
            file_path = Path(m.file)
            if file_path.is_file():
                lines = read_lines(file_path)
                body, _ = extract_method_body(lines, m.line - 1)
                entry.update(analyze_method_body(body, method_name))

        results.append(entry)

    results.sort(key=lambda r: (r.get("load_rank", 0), r.get("line", 0)))

    payload = {
        "model": model_name,
        "method": method_name,
        "overrides": results[:max_results],
        "overrides_total": len(results),
        "overrides_truncated": len(results) > max_results,
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)


def trace_method_chain(paths: OdooPaths, model_name: str, method_name: str) -> str:
    """Chaîne d'exécution approximative des surcharges (ordre de chargement + super())."""
    raw = json.loads(find_method_overrides(paths, model_name, method_name, analyze_body=True))
    chain: list[dict] = []
    for i, ov in enumerate(raw.get("overrides", [])):
        step = {
            "order": i + 1,
            "module": ov.get("module"),
            "file": ov.get("file"),
            "line": ov.get("line"),
            "kind": ov.get("kind"),
            "timing": ov.get("timing", "unknown"),
            "calls_super": ov.get("calls_super", False),
            "self_method_calls": ov.get("self_method_calls", []),
            "side_effect_calls": ov.get("side_effect_calls", []),
            "load_rank": ov.get("load_rank"),
        }
        if ov.get("calls_super"):
            step["execution_note"] = "Calls super() — runs base chain then adds behavior"
        elif ov.get("kind") == "override":
            step["execution_note"] = "Override without super() — may replace prior behavior"
        chain.append(step)

    payload = {
        "model": model_name,
        "method": method_name,
        "chain": chain,
        "note": "Order approximates module dependency load order. Odoo MRO also depends on _inherit list order.",
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)


def find_method_callers(
    paths: OdooPaths,
    model_name: str,
    method_name: str,
    *,
    max_results: int = 50,
) -> str:
    search_paths = [p for p in paths.search_roots if p.is_dir()]
    python_callers: list[dict] = []
    xml_callers: list[dict] = []
    seen: set[tuple[str, int, str]] = set()

    py_patterns = [
        rf"\.{re.escape(method_name)}\s*\(",
        rf"self\.{re.escape(method_name)}\s*\(",
    ]
    for pattern in py_patterns:
        matches, _ = rg_tools.run_rg(
            paths, pattern, search_paths, scope="all", glob="**/*.py", file_type="py", max_results=200
        )
        for m in matches:
            if re.match(rf"^\s*def\s+{re.escape(method_name)}\s*\(", m.content):
                continue
            key = (m.relative_path, m.line, "py")
            if key in seen:
                continue
            seen.add(key)
            python_callers.append(
                {
                    "source": "python",
                    "file": m.relative_path,
                    "workspace_hint": m.workspace_hint,
                    "line": m.line,
                    "module": module_from_path(paths, Path(m.file)),
                    "content": m.content.strip(),
                }
            )

    xml_patterns = [
        rf'name="{re.escape(method_name)}"',
        rf"name='{re.escape(method_name)}'",
    ]
    for pattern in xml_patterns:
        matches, _ = rg_tools.run_rg(
            paths, pattern, search_paths, scope="all", glob="**/*.xml", file_type=None, max_results=100, fixed_string=True
        )
        for m in matches:
            key = (m.relative_path, m.line, "xml")
            if key in seen:
                continue
            if model_name and f'model="{model_name}"' not in m.content and f"model='{model_name}'" not in m.content:
                # keep buttons without model attr on same file search
                pass
            seen.add(key)
            xml_callers.append(
                {
                    "source": "xml",
                    "file": m.relative_path,
                    "workspace_hint": m.workspace_hint,
                    "line": m.line,
                    "content": m.content.strip(),
                    "action_type": "object" if "type=\"object\"" in m.content or "type='object'" in m.content else "unknown",
                }
            )

    all_callers = (python_callers + xml_callers)[:max_results]
    payload = {
        "model": model_name,
        "method": method_name,
        "callers": all_callers,
        "python_callers_total": len(python_callers),
        "xml_callers_total": len(xml_callers),
        "callers_truncated": len(python_callers) + len(xml_callers) > max_results,
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)
