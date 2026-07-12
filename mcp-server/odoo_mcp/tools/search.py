"""Outils de recherche ripgrep/glob optimisés pour les grosses codebases."""

from __future__ import annotations

import json
from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.security import PathSecurityError, resolve_safe_path, resolve_search_path
from odoo_mcp.tools import rg as rg_tools
from odoo_mcp.tools.paths_util import normalize_result_path

MAX_OUTPUT_CHARS = 80_000
DEFAULT_MAX_RESULTS = 100


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... [truncated — {len(text) - limit} chars omitted]"


def _coalesce(*values: str | None) -> str | None:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def search_code(
    paths: OdooPaths,
    pattern: str | None = None,
    *,
    query: str | None = None,
    scope: str = "all",
    glob: str | None = None,
    glob_pattern: str | None = None,
    file_type: str | None = "py",
    context: int = 0,
    max_results: int = DEFAULT_MAX_RESULTS,
    case_insensitive: bool = False,
    fixed_string: bool = False,
) -> str:
    effective_pattern = _coalesce(pattern, query)
    if not effective_pattern:
        return "Error: provide 'pattern' or 'query'."

    effective_glob = _coalesce(glob_pattern, glob)
    try:
        search_paths = resolve_search_path(paths, scope)
    except PathSecurityError as exc:
        return f"Error: {exc}"

    matches, warning = rg_tools.run_rg(
        paths,
        effective_pattern,
        search_paths,
        scope=scope,
        glob=effective_glob,
        file_type=file_type,
        context=context,
        max_results=max_results,
        case_insensitive=case_insensitive,
        fixed_string=fixed_string,
    )
    return rg_tools.format_matches(
        matches,
        pattern=effective_pattern,
        max_results=max_results,
        warning=warning,
    )


def glob_files(
    paths: OdooPaths,
    pattern: str | None = None,
    *,
    glob_pattern: str | None = None,
    scope: str = "all",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> str:
    effective_pattern = _coalesce(pattern, glob_pattern)
    if not effective_pattern:
        return "Error: provide 'pattern' or 'glob_pattern'."

    try:
        search_paths = resolve_search_path(paths, scope)
    except PathSecurityError as exc:
        return f"Error: {exc}"

    if not search_paths:
        return f"No directory available for scope {scope!r}."

    from odoo_mcp.tools.paths_util import enterprise_warning

    warning = enterprise_warning(paths, scope)
    glob_arg = effective_pattern if effective_pattern.startswith("**/") else f"**/{effective_pattern}"
    found: list[dict[str, str]] = []

    import shutil
    import subprocess

    if shutil.which("rg"):
        cmd = ["rg", "--files", "-g", glob_arg, "--glob", "!.git/**"]
        cmd.extend(str(p) for p in search_paths if p.is_dir())
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode in (0, 1):
                for line in result.stdout.splitlines():
                    path = Path(line.strip())
                    if path.is_file():
                        found.append(normalize_result_path(path, paths))
                        if len(found) >= max_results:
                            break
        except subprocess.TimeoutExpired:
            pass

    if not found:
        for root in search_paths:
            for match in root.glob(glob_arg):
                if match.is_file():
                    found.append(normalize_result_path(match, paths))
                    if len(found) >= max_results:
                        break
            if len(found) >= max_results:
                break

    if not found:
        msg = f"No files for pattern {effective_pattern!r}."
        return f"{warning}\n\n{msg}" if warning else msg

    lines = [
        f"{item['relative_path']}  (workspace: {item['workspace_hint']})"
        for item in sorted(found, key=lambda x: x["relative_path"])
    ]
    text = _truncate(f"{len(lines)} file(s)\n\n" + "\n".join(lines))
    return f"{warning}\n\n{text}" if warning else text


def read_file(
    paths: OdooPaths,
    path: str,
    *,
    offset: int = 1,
    limit: int = 200,
) -> str:
    file_path = resolve_safe_path(paths, path)
    if not file_path.is_file():
        raise PathSecurityError(f"Not a file: {path}")

    info = normalize_result_path(file_path, paths)
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    start = max(0, offset - 1)
    end = min(total, start + limit)
    selected = lines[start:end]

    numbered = [f"{i + start + 1:6}| {line}" for i, line in enumerate(selected)]
    header = (
        f"File: {info['relative_path']}\n"
        f"Workspace hint: {info['workspace_hint']}\n"
        f"Also accepted: {info['workspace_hint']}, community/{info['relative_path'].split('/', 1)[-1] if '/' in info['relative_path'] else info['relative_path']}\n"
        f"Lines {start + 1}-{end} of {total}\n\n"
    )
    return _truncate(header + "\n".join(numbered))


def list_directory(
    paths: OdooPaths,
    path: str = "",
    *,
    max_entries: int = 200,
) -> str:
    if not path:
        entries = []
        for root in paths.search_roots:
            if root.is_dir():
                label = "community" if root == paths.community else "enterprise"
                entries.append(f"[{label}] {root}/")
        if not entries:
            return "No Odoo codebase mounted. Check Git clone."
        return "\n".join(entries)

    dir_path = resolve_safe_path(paths, path)
    if not dir_path.is_dir():
        raise PathSecurityError(f"Not a directory: {path}")

    items: list[str] = []
    for entry in sorted(dir_path.iterdir()):
        suffix = "/" if entry.is_dir() else ""
        items.append(entry.name + suffix)
        if len(items) >= max_entries:
            items.append(f"... [{dir_path} contains more than {max_entries} entries]")
            break
    info = normalize_result_path(dir_path, paths)
    return f"{info['relative_path']}/ (workspace: {info['workspace_hint']})\n\n" + "\n".join(items)
