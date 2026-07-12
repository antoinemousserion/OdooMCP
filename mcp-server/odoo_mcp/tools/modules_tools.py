"""Outils modules : dépendances, modèles, impact."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.tools import rg as rg_tools
from odoo_mcp.tools.model_utils import (
    _NAME_RE,
    _INHERIT_SINGLE_RE,
    json_result,
    load_module_depends,
    module_from_path,
    read_lines,
    resolve_path_from_relative,
)
from odoo_mcp.tools.paths_util import enterprise_warning, normalize_result_path


def get_module_dependencies(paths: OdooPaths, module_name: str) -> str:
    from odoo_mcp.tools.odoo import get_module_dependencies as _base

    return _base(paths, module_name)


def get_dependent_modules(paths: OdooPaths, module_name: str) -> str:
    depends_map = load_module_depends(paths)
    dependents: list[dict] = []

    for mod, deps in depends_map.items():
        if module_name in deps:
            dependents.append({"module": mod, "depends_on": module_name, "full_depends": deps})

    payload = {
        "module": module_name,
        "dependent_modules": sorted(dependents, key=lambda x: x["module"]),
        "dependent_total": len(dependents),
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)


def get_module_models(paths: OdooPaths, module_name: str) -> str:
    try:
        from odoo_mcp.security import resolve_search_path

        search_paths = resolve_search_path(paths, module_name)
    except Exception as exc:
        return json_result({"module": module_name, "error": str(exc)})

    defined: list[dict] = []
    extended: list[dict] = []
    seen_def: set[str] = set()
    seen_ext: set[str] = set()

    for root in search_paths:
        for py_file in root.rglob("models/*.py"):
            if not py_file.is_file():
                continue
            info = normalize_result_path(py_file, paths)
            lines = read_lines(py_file)
            for idx, line in enumerate(lines, 1):
                nm = _NAME_RE.search(line)
                if nm and nm.group(1) not in seen_def:
                    seen_def.add(nm.group(1))
                    defined.append({"model": nm.group(1), "file": info["relative_path"], "line": idx})
                im = _INHERIT_SINGLE_RE.search(line)
                if im:
                    key = (im.group(1), info["relative_path"])
                    if key not in seen_ext:
                        seen_ext.add(key)
                        extended.append(
                            {"model": im.group(1), "file": info["relative_path"], "line": idx, "style": "inherit"}
                        )

    payload = {
        "module": module_name,
        "models_defined": defined,
        "models_extended": extended,
        "models_defined_total": len(defined),
        "models_extended_total": len(extended),
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)
