"""Outils vues XML, boutons, actions."""

from __future__ import annotations

import re

from odoo_mcp.config import OdooPaths
from odoo_mcp.tools import rg as rg_tools
from odoo_mcp.tools.model_utils import json_result, module_from_path, read_lines, resolve_path_from_relative
from odoo_mcp.tools.paths_util import enterprise_warning


def find_view_for_model(paths: OdooPaths, model_name: str, *, max_results: int = 50) -> str:
    pattern = rf'model="{re.escape(model_name)}"'
    alt = rf"model='{re.escape(model_name)}'"
    results: list[dict] = []
    seen: set[tuple[str, int]] = set()
    search_paths = [p for p in paths.search_roots if p.is_dir()]

    for pat in (pattern, alt):
        matches, _ = rg_tools.run_rg(
            paths, pat, search_paths, scope="all", glob="**/*.xml", file_type=None, max_results=200, fixed_string=True
        )
        for m in matches:
            key = (m.relative_path, m.line)
            if key in seen:
                continue
            seen.add(key)
            view_type = "unknown"
            for vt in ("form", "tree", "kanban", "search", "calendar", "graph", "pivot"):
                if f"<{vt}" in m.content or f"<{vt} " in m.content:
                    view_type = vt
                    break
            results.append(
                {
                    "file": m.relative_path,
                    "workspace_hint": m.workspace_hint,
                    "line": m.line,
                    "module": module_from_path(paths, __import__("pathlib").Path(m.file)),
                    "view_type": view_type,
                    "content": m.content.strip()[:300],
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
    patterns = [f'name="{button_name}"', f"name='{button_name}'"]
    results: list[dict] = []
    search_paths = [p for p in paths.search_roots if p.is_dir()]

    for pat in patterns:
        matches, _ = rg_tools.run_rg(
            paths, pat, search_paths, scope="all", glob="**/*.xml", file_type=None, max_results=50, fixed_string=True
        )
        for m in matches:
            if model_name and f'model="{model_name}"' not in m.content and f"model='{model_name}'" not in m.content:
                # include if file likely belongs to model module
                if model_name.split(".")[0] not in m.relative_path:
                    continue
            attrs = _parse_xml_attrs(m.content)
            results.append(
                {
                    "file": m.relative_path,
                    "workspace_hint": m.workspace_hint,
                    "line": m.line,
                    "button_name": button_name,
                    "attrs": attrs,
                    "content": m.content.strip()[:400],
                }
            )

    payload = {"model": model_name, "button_name": button_name, "buttons": results}
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)


def _parse_xml_attrs(line: str) -> dict:
    attrs: dict = {}
    for key in ("name", "type", "string", "class", "icon", "confirm", "context", "groups", "invisible", "readonly", "states"):
        m = re.search(rf'{key}="([^"]*)"', line)
        if m:
            attrs[key] = m.group(1)
        m2 = re.search(rf"{key}='([^']*)'", line)
        if m2:
            attrs[key] = m2.group(1)
    return attrs


def find_window_action(paths: OdooPaths, model_name: str, *, max_results: int = 30) -> str:
    results: list[dict] = []
    seen: set[tuple[str, int]] = set()
    search_paths = [p for p in paths.search_roots if p.is_dir()]

    matches, _ = rg_tools.run_rg(
        paths,
        re.escape(model_name),
        search_paths,
        scope="all",
        glob="**/*.xml",
        file_type=None,
        max_results=200,
        fixed_string=True,
    )
    for m in matches:
        if model_name not in m.content:
            continue
        if "act_window" not in m.content and "res_model" not in m.content:
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
                "content": m.content.strip()[:400],
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
    """Résout inherit_id dans un fichier vue ou cherche par xmlid."""
    search_paths = [p for p in paths.search_roots if p.is_dir()]
    chain: list[dict] = []

    if file_hint:
        file_path = resolve_path_from_relative(paths, file_hint)
        if file_path and file_path.is_file():
            chain.extend(_inherit_chain_from_file(file_path, paths))

    if view_xml_id:
        pat = re.escape(view_xml_id.split(".")[-1])
        matches, _ = rg_tools.run_rg(
            paths, pat, search_paths, scope="all", glob="**/*.xml", max_results=20, fixed_string=True
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
            chain.append({"inherits_from": m.group(1), "line": i, "content": line.strip()})
    return chain


def find_xml_action(
    paths: OdooPaths,
    action_name: str,
    *,
    model: str | None = None,
    max_results: int = 30,
) -> str:
    from odoo_mcp.tools.odoo import find_xml_action as _base

    return _base(paths, action_name, model=model, max_results=max_results)
