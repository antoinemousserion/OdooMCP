"""Super-tools : explore_model, explore_method, explore_module."""

from __future__ import annotations

import json
import re

from odoo_mcp.config import OdooPaths
from odoo_mcp.tools import fields_tools, methods, modules_tools, views
from odoo_mcp.tools.model_utils import files_for_model, json_result, read_lines, resolve_path_from_relative
from odoo_mcp.tools.odoo import find_model, get_model_inheritance_chain, get_module_info
from odoo_mcp.tools.paths_util import enterprise_warning


def _action_methods_for_model(paths: OdooPaths, model_name: str) -> list[str]:
    names: set[str] = set()
    for rel in files_for_model(paths, model_name):
        file_path = resolve_path_from_relative(paths, rel)
        if not file_path:
            continue
        for line in read_lines(file_path):
            m = re.match(r"^\s*def\s+(action_\w+|button_\w+)\s*\(", line)
            if m:
                names.add(m.group(1))
    return sorted(names)[:20]


def _safe_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def explore_model(paths: OdooPaths, model_name: str) -> str:
    model_data = _safe_json(find_model(paths, model_name=model_name))
    fields_data = _safe_json(fields_tools.list_model_fields(paths, model_name, max_fields=80))
    mixins_data = _safe_json(fields_tools.find_model_mixins(paths, model_name))
    inheritance_data = _safe_json(get_model_inheritance_chain(paths, model_name))
    views_data = _safe_json(views.find_view_for_model(paths, model_name, max_results=20))

    method_overrides_sample: dict = {}
    for method in ("action_confirm", "write", "create", "unlink"):
        ov = _safe_json(methods.find_method_overrides(paths, model_name, method, max_results=10))
        if ov.get("overrides"):
            method_overrides_sample[method] = ov["overrides"][:5]

    payload = {
        "model": model_name,
        "definition": model_data,
        "fields_summary": fields_data.get("summary"),
        "fields_count": fields_data.get("fields_total"),
        "fields_sample": fields_data.get("fields", [])[:30],
        "mixins": mixins_data.get("mixins", []),
        "inheritance": inheritance_data,
        "views": views_data.get("views", [])[:15],
        "action_methods": _action_methods_for_model(paths, model_name),
        "method_overrides_sample": method_overrides_sample,
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)


def explore_method(paths: OdooPaths, model_name: str, method_name: str) -> str:
    overrides = _safe_json(methods.find_method_overrides(paths, model_name, method_name))
    chain = _safe_json(methods.trace_method_chain(paths, model_name, method_name))
    callers = _safe_json(methods.find_method_callers(paths, model_name, method_name))
    xml_actions = _safe_json(views.find_xml_action(paths, method_name, model=model_name))

    payload = {
        "model": model_name,
        "method": method_name,
        "overrides": overrides,
        "execution_chain": chain,
        "callers": callers,
        "xml_buttons": xml_actions,
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)


def explore_module(paths: OdooPaths, module_name: str) -> str:
    info = _safe_json(get_module_info(paths, module_name))
    deps = _safe_json(modules_tools.get_module_dependencies(paths, module_name))
    dependents = _safe_json(modules_tools.get_dependent_modules(paths, module_name))
    models = _safe_json(modules_tools.get_module_models(paths, module_name))

    payload = {
        "module": module_name,
        "manifest": info,
        "dependencies": deps,
        "dependent_modules": dependents,
        "models": models,
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)
