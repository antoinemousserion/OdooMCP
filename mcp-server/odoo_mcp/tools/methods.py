"""Outils méthodes : overrides, chaîne d'appels, callers."""

from __future__ import annotations

import json
import re
from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.security import resolve_search_path
from odoo_mcp.tools import rg as rg_tools
from odoo_mcp.tools.model_utils import (
    analyze_method_body,
    extract_method_body,
    json_result,
    load_module_depends,
    module_from_path,
    module_load_rank,
    read_lines,
    resolve_path_from_relative,
    scan_model_matches,
)
from odoo_mcp.tools.paths_util import enterprise_warning

_TEST_PATH_RE = re.compile(r"(^|/)(tests?|static/tests?)/|/test_[^/]+\.py$|_tests\.py$")


def _is_test_path(relative_path: str) -> bool:
    return bool(_TEST_PATH_RE.search(relative_path.replace("\\", "/")))


def _model_file_paths(paths: OdooPaths, model_name: str, related_files: set[str]) -> list[Path]:
    file_paths: list[Path] = []
    for rel in related_files:
        fp = resolve_path_from_relative(paths, rel)
        if fp and fp.is_file():
            file_paths.append(fp)
    if file_paths:
        return file_paths
    try:
        return resolve_search_path(paths, model_name.split(".")[0])
    except Exception:
        return resolve_search_path(paths, "all")


def _collect_overrides(
    paths: OdooPaths,
    model_name: str,
    method_name: str,
    *,
    max_results: int = 30,
    analyze_body: bool = True,
) -> list[dict]:
    definitions, inherits = scan_model_matches(paths, model_name)
    related_files = {item["file"] for item in definitions + inherits}
    base_files = {d["file"] for d in definitions if d["definition_kind"] == "canonical"}
    search_paths = _model_file_paths(paths, model_name, related_files)

    method_pattern = rf"^\s*def\s+{re.escape(method_name)}\s*\("
    matches, _ = rg_tools.run_rg(
        paths,
        method_pattern,
        search_paths,
        scope="all",
        glob="**/models/*.py",
        file_type="py",
        max_results=100,
    )

    depends_map = load_module_depends(paths)
    results: list[dict] = []
    seen: set[tuple[str, int]] = set()

    for m in matches:
        key = (m.relative_path, m.line)
        if key in seen:
            continue
        seen.add(key)

        entry: dict = {
            "file": m.relative_path,
            "workspace_hint": m.workspace_hint,
            "line": m.line,
            "module": module_from_path(paths, Path(m.file)),
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
    return results[:max_results]


def find_method_overrides(
    paths: OdooPaths,
    model_name: str,
    method_name: str,
    *,
    max_results: int = 30,
    analyze_body: bool = True,
) -> str:
    results = _collect_overrides(
        paths, model_name, method_name, max_results=max_results, analyze_body=analyze_body
    )
    payload = {
        "model": model_name,
        "method": method_name,
        "overrides": results,
        "overrides_total": len(results),
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)


def trace_method_chain(paths: OdooPaths, model_name: str, method_name: str) -> str:
    overrides = _collect_overrides(paths, model_name, method_name, max_results=30, analyze_body=True)
    chain: list[dict] = []
    for i, ov in enumerate(overrides):
        step = {
            "order": i + 1,
            "module": ov.get("module"),
            "file": ov.get("file"),
            "line": ov.get("line"),
            "kind": ov.get("kind"),
            "timing": ov.get("timing", "unknown"),
            "calls_super": ov.get("calls_super", False),
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
        "note": "Order approximates module dependency load order.",
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
    max_results: int = 20,
    include_tests: bool = False,
) -> str:
    module = model_name.split(".")[0]
    try:
        search_paths = resolve_search_path(paths, module)
    except Exception:
        search_paths = resolve_search_path(paths, "all")

    python_callers: list[dict] = []
    xml_callers: list[dict] = []
    seen: set[tuple[str, int, str]] = set()

    py_pattern = rf"\.{re.escape(method_name)}\s*\("
    matches, _ = rg_tools.run_rg(
        paths,
        py_pattern,
        search_paths,
        scope="all",
        glob="**/*.py",
        file_type="py",
        max_results=80,
    )
    for m in matches:
        if re.match(rf"^\s*def\s+{re.escape(method_name)}\s*\(", m.content):
            continue
        if not include_tests and _is_test_path(m.relative_path):
            continue
        key = (m.relative_path, m.line, "py")
        if key in seen:
            continue
        seen.add(key)
        python_callers.append(
            {
                "source": "python",
                "file": m.relative_path,
                "line": m.line,
                "module": module_from_path(paths, Path(m.file)),
                "content": m.content.strip()[:200],
            }
        )

    xml_patterns = [f'name="{method_name}"', f"name='{method_name}'"]
    matches, _ = rg_tools.run_rg(
        paths,
        xml_patterns[0],
        search_paths,
        scope="all",
        glob="**/views/**/*.xml",
        file_type=None,
        max_results=30,
        fixed_string=True,
        patterns=xml_patterns,
    )
    for m in matches:
        key = (m.relative_path, m.line, "xml")
        if key in seen:
            continue
        if model_name.split(".")[0] not in m.relative_path:
            continue
        seen.add(key)
        xml_callers.append(
            {
                "source": "xml",
                "file": m.relative_path,
                "line": m.line,
                "content": m.content.strip()[:200],
                "action_type": "object" if 'type="object"' in m.content or "type='object'" in m.content else "unknown",
            }
        )

    all_callers = (python_callers + xml_callers)[:max_results]
    payload = {
        "model": model_name,
        "method": method_name,
        "callers": all_callers,
        "python_callers_total": len(python_callers),
        "xml_callers_total": len(xml_callers),
        "tests_excluded": not include_tests,
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)
