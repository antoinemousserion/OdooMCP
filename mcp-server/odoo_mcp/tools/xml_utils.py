"""Utilitaires XML Odoo (vues, boutons, records)."""

from __future__ import annotations

import re
from pathlib import Path

from odoo_mcp.tools.model_utils import read_lines

_VIEW_TYPES = ("form", "tree", "kanban", "search", "calendar", "graph", "pivot", "activity")


def model_xml_patterns(model_name: str) -> list[str]:
    """Patterns rg fixed-string pour lier un modèle dans les XML Odoo."""
    escaped = model_name
    return [
        f'<field name="model">{escaped}</field>',
        f"<field name='model'>{escaped}</field>",
        f'name="model">{escaped}<',
        f"name='model'>{escaped}<",
        f'model="{escaped}"',
        f"model='{escaped}'",
    ]


def res_model_xml_patterns(model_name: str) -> list[str]:
    escaped = model_name
    return [
        f'<field name="res_model">{escaped}</field>',
        f'name="res_model">{escaped}<',
        f"res_model=\"{escaped}\"",
        f"res_model='{escaped}'",
    ]


def detect_view_type(text: str) -> str:
    for vt in _VIEW_TYPES:
        if f"<{vt}" in text or f"<{vt} " in text:
            return vt
    if "ir.ui.view" in text or "ir.actions.act_window" in text:
        return "record"
    return "unknown"


def file_declares_model(file_path: Path, model_name: str) -> bool:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")[:12000]
    except OSError:
        return False
    return any(p in text for p in model_xml_patterns(model_name))


def extract_xml_element(lines: list[str], start_idx: int, *, max_lines: int = 12) -> str:
    """Reconstitue un élément XML sur plusieurs lignes (ex. bouton)."""
    parts: list[str] = []
    end = min(len(lines), start_idx + max_lines)
    for i in range(start_idx, end):
        parts.append(lines[i].strip())
        combined = " ".join(parts)
        if combined.endswith("/>") or "</button>" in combined or "</xpath>" in combined:
            break
        if "<button" in combined and combined.count(">") >= 2 and combined.rstrip().endswith(">"):
            if not combined.endswith("<"):
                break
    return " ".join(parts)


def parse_xml_attrs(fragment: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key in (
        "name", "type", "string", "class", "icon", "confirm",
        "context", "groups", "invisible", "readonly", "states", "id",
    ):
        for quote in ('"', "'"):
            m = re.search(rf"{key}={quote}([^'{quote}]*){quote}", fragment)
            if m:
                attrs[key] = m.group(1)
    return attrs
