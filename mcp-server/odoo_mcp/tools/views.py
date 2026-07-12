"""Outils vues XML, boutons, actions."""

from __future__ import annotations

import re
from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.security import resolve_search_path
from odoo_mcp.tools import rg as rg_tools
from odoo_mcp.tools.model_utils import json_result, module_from_path, read_lines, resolve_path_from_relative
from odoo_mcp.tools.paths_util import enterprise_warning
from odoo_mcp.tools.xml_utils import (
    detect_view_type,
    extract_xml_element,
    file_declares_model,
    model_xml_patterns,
    parse_xml_attrs,
    res_model_xml_patterns,
)


def _module_scope(paths: OdooPaths, model_name: str) -> list[Path]:
    module = model_name.split(".")[0]
    try:
        return resolve_search_path(paths, module)
    except Exception:
        return resolve_search_path(paths, "all")


def find_view_for_model(paths: OdooPaths, model_name: str, *, max_results: int = 30) -> str:
    search_paths = _module_scope(paths, model_name)
    patterns = model_xml_patterns(model_name)
    results: list[dict] = []
    seen: set[tuple[str, int]] = set()

    matches, _ = rg_tools.run_rg(
        paths,
        patterns[0],
        search_paths,
        scope="all",
        glob="**/views/**/*.xml",
        file_type=None,
        max_results=max_results * 3,
        fixed_string=True,
        patterns=patterns,
    )
    for m in matches:
        key = (m.relative_path, m.line)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "file": m.relative_path,
                "workspace_hint": m.workspace_hint,
                "line": m.line,
                "module": module_from_path(paths, Path(m.file)),
                "view_type": detect_view_type(m.content),
                "content": m.content.strip()[:200],
            }
        )

    payload = {
        "model": model_name,
        "views": results[:max_results],
        "views_total": len(results),
        "views_truncated": len(results) > max_results,
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)


def get_button_context(
    paths: OdooPaths,
    model_name: str,
    button_name: str,
) -> str:
    search_paths = _module_scope(paths, model_name)
    patterns = [f'name="{button_name}"', f"name='{button_name}'"]
    results: list[dict] = []

    matches, _ = rg_tools.run_rg(
        paths,
        patterns[0],
        search_paths,
        scope="all",
        glob="**/views/**/*.xml",
        file_type=None,
        max_results=30,
        fixed_string=True,
        patterns=patterns,
    )
    for m in matches:
        file_path = Path(m.file)
        if model_name and not file_declares_model(file_path, model_name):
            if model_name.split(".")[0] not in m.relative_path:
                continue
        lines = read_lines(file_path)
        fragment = extract_xml_element(lines, m.line - 1)
        attrs = parse_xml_attrs(fragment)
        results.append(
            {
                "file": m.relative_path,
                "workspace_hint": m.workspace_hint,
                "line": m.line,
                "button_name": button_name,
                "attrs": attrs,
                "content": fragment[:400],
            }
        )

    payload = {"model": model_name, "button_name": button_name, "buttons": results}
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)


def find_window_action(paths: OdooPaths, model_name: str, *, max_results: int = 20) -> str:
    search_paths = _module_scope(paths, model_name)
    patterns = res_model_xml_patterns(model_name)
    results: list[dict] = []
    seen: set[tuple[str, int]] = set()

    matches, _ = rg_tools.run_rg(
        paths,
        patterns[0],
        search_paths,
        scope="all",
        glob="**/*.xml",
        file_type=None,
        max_results=max_results * 3,
        fixed_string=True,
        patterns=patterns,
    )
    for m in matches:
        if "act_window" not in m.content and "ir.actions.act_window" not in m.content:
            # keep field-only lines in action xml files
            if "res_model" not in m.content:
                continue
        key = (m.relative_path, m.line)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "file": m.relative_path,
                "workspace_hint": m.workspace_hint,
                "line": m.line,
                "content": m.content.strip()[:250],
            }
        )

    payload = {
        "model": model_name,
        "window_actions": results[:max_results],
        "total": len(results),
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)


def resolve_view_inheritance(paths: OdooPaths, view_xml_id: str | None = None, *, file_hint: str | None = None) -> str:
    search_paths = resolve_search_path(paths, "all")
    chain: list[dict] = []

    if file_hint:
        file_path = resolve_path_from_relative(paths, file_hint)
        if file_path and file_path.is_file():
            chain.extend(_inherit_chain_from_file(file_path, paths))

    if view_xml_id:
        pat = re.escape(view_xml_id.split(".")[-1])
        matches, _ = rg_tools.run_rg(
            paths, pat, search_paths, scope="all", glob="**/views/**/*.xml", max_results=10, fixed_string=True
        )
        for m in matches:
            if view_xml_id.replace(".", "_") in m.content or view_xml_id in m.content:
                fp = resolve_path_from_relative(paths, m.relative_path)
                if fp:
                    chain.extend(_inherit_chain_from_file(fp, paths))

    payload = {"view_xml_id": view_xml_id, "file_hint": file_hint, "inheritance_chain": chain}
    return json_result(payload)


def _inherit_chain_from_file(file_path, paths) -> list[dict]:
    from odoo_mcp.tools.paths_util import normalize_result_path

    lines = read_lines(file_path)
    chain = [{"file": normalize_result_path(file_path, paths)["relative_path"], "role": "target"}]
    for i, line in enumerate(lines, 1):
        m = re.search(r'inherit_id="([^"]+)"', line) or re.search(r"inherit_id='([^']+)'", line)
        if m:
            chain.append({"inherits_from": m.group(1), "line": i, "content": line.strip()[:200]})
    return chain


def find_xml_action(
    paths: OdooPaths,
    action_name: str,
    *,
    model: str | None = None,
    max_results: int = 20,
) -> str:
    from odoo_mcp.tools.odoo import find_xml_action as _base

    return _base(paths, action_name, model=model, max_results=max_results)
