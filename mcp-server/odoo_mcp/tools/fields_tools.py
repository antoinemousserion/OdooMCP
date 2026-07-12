"""Outils champs : liste, compute dependencies."""

from __future__ import annotations

import re
from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.tools import rg as rg_tools
from odoo_mcp.tools.model_utils import (
    _DEPENDS_RE,
    _FIELD_LINE_RE,
    analyze_method_body,
    extract_method_body,
    files_for_model,
    json_result,
    module_from_path,
    parse_field_attributes,
    read_lines,
    resolve_path_from_relative,
    scan_model_matches,
)
from odoo_mcp.tools.paths_util import enterprise_warning


def _collect_fields_from_file(path: Path, paths: OdooPaths) -> list[dict]:
    lines = read_lines(path)
    fields: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        line_no = i + 1
        m = _FIELD_LINE_RE.match(line)
        if not m or _is_assignment(line, m.group(1)):
            i += 1
            continue
        name, ftype = m.group(1), m.group(2)
        if name.startswith("_") and name in ("_name", "_inherit", "_description"):
            i += 1
            continue
        continuation = ""
        j = i + 1
        combined = line
        if line.count("(") > line.count(")"):
            while j < len(lines):
                continuation += " " + lines[j].strip()
                combined = line + continuation
                if combined.count("(") <= combined.count(")"):
                    j += 1
                    break
                j += 1
        attrs = parse_field_attributes(line, continuation)
        fields.append(
            {
                "name": name,
                "type": ftype,
                "line": line_no,
                "file": normalize_path(paths, path),
                "module": module_from_path(paths, path),
                **attrs,
            }
        )
        i = j if j > i + 1 else i + 1
    return fields


def _is_assignment(line: str, field_name: str) -> bool:
    return bool(re.match(rf"^\s*\w+\.{re.escape(field_name)}\s*=", line))


def normalize_path(paths: OdooPaths, path: Path) -> str:
    from odoo_mcp.tools.paths_util import normalize_result_path

    return normalize_result_path(path, paths)["relative_path"]


def list_model_fields(paths: OdooPaths, model_name: str, *, max_fields: int = 200) -> str:
    model_files = files_for_model(paths, model_name)
    definitions, inherits = scan_model_matches(paths, model_name)

    all_fields: list[dict] = []
    seen_names: dict[str, dict] = {}

    for rel in sorted(model_files):
        file_path = resolve_path_from_relative(paths, rel)
        if not file_path or not file_path.is_file():
            continue
        for field in _collect_fields_from_file(file_path, paths):
            field["model"] = model_name
            field["from_inherit"] = rel not in {d["file"] for d in definitions}
            seen_names[field["name"]] = field

    all_fields = list(seen_names.values())[:max_fields]

    computed = [f for f in all_fields if f.get("is_computed") or f.get("compute")]
    related = [f for f in all_fields if f.get("is_related") or f.get("related")]
    tracked = [f for f in all_fields if f.get("tracking")]

    payload = {
        "model": model_name,
        "fields_total": len(seen_names),
        "fields": all_fields,
        "fields_truncated": len(seen_names) > max_fields,
        "summary": {
            "computed": [f["name"] for f in computed],
            "related": [f["name"] for f in related],
            "tracked": [f["name"] for f in tracked],
        },
        "inheritance_files": sorted(model_files),
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)


def find_compute_dependencies(
    paths: OdooPaths,
    model_name: str,
    field_name: str,
) -> str:
    fields_data = __import__("json").loads(list_model_fields(paths, model_name))
    target = next((f for f in fields_data.get("fields", []) if f["name"] == field_name), None)

    if not target:
        payload = {"model": model_name, "field": field_name, "error": "Field not found on model."}
        return json_result(payload)

    compute_method = target.get("compute")
    if not compute_method and not target.get("is_computed"):
        payload = {
            "model": model_name,
            "field": field_name,
            "message": "Field is not computed.",
            "field_info": target,
        }
        return json_result(payload)

    if compute_method and not compute_method.startswith("_compute"):
        compute_method = f"_compute_{field_name}" if not compute_method else compute_method

    model_files = files_for_model(paths, model_name)
    depends_found: list[dict] = []

    for rel in model_files:
        file_path = resolve_path_from_relative(paths, rel)
        if not file_path:
            continue
        lines = read_lines(file_path)
        for idx, line in enumerate(lines):
            if compute_method and f"def {compute_method}" in line:
                body, _ = extract_method_body(lines, idx)
                for dm in _DEPENDS_RE.finditer(body):
                    depends_found.append(
                        {
                            "file": rel,
                            "line": idx + 1,
                            "compute_method": compute_method,
                            "depends": dm.group(1).strip(),
                        }
                    )
            if f"def _compute_{field_name}" in line:
                body, _ = extract_method_body(lines, idx)
                for dm in _DEPENDS_RE.finditer(body):
                    depends_found.append(
                        {
                            "file": rel,
                            "line": idx + 1,
                            "compute_method": f"_compute_{field_name}",
                            "depends": dm.group(1).strip(),
                        }
                    )

    payload = {
        "model": model_name,
        "field": field_name,
        "field_info": target,
        "compute_method": compute_method or f"_compute_{field_name}",
        "depends": depends_found,
    }
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)


def find_model_mixins(paths: OdooPaths, model_name: str) -> str:
    model_files = files_for_model(paths, model_name)
    mixins: list[dict] = []
    seen: set[str] = set()

    for rel in model_files:
        file_path = resolve_path_from_relative(paths, rel)
        if not file_path:
            continue
        text = read_lines(file_path)
        content = "\n".join(text)
        module = module_from_path(paths, file_path)

        for line_no, line in enumerate(text, 1):
            single = re.search(r"""_inherit\s*=\s*['"]([^'"]+)['"]""", line)
            if single:
                val = single.group(1)
                if val != model_name and val not in seen:
                    seen.add(val)
                    mixins.append({"mixin": val, "file": rel, "line": line_no, "module": module, "style": "single"})
            list_m = re.search(r"""_inherit\s*=\s*\[(.*?)\]""", line)
            if list_m:
                for val in re.findall(r"""['"]([^'"]+)['"]""", list_m.group(1)):
                    if val != model_name and val not in seen:
                        seen.add(val)
                        mixins.append({"mixin": val, "file": rel, "line": line_no, "module": module, "style": "list"})

    payload = {"model": model_name, "mixins": mixins, "mixins_total": len(mixins)}
    warning = enterprise_warning(paths, "all")
    if warning:
        payload["warning"] = warning
    return json_result(payload)
