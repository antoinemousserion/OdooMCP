"""Outils de recherche ripgrep/glob optimisés pour les grosses codebases."""

from __future__ import annotations

import json
from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.security import PathSecurityError, resolve_safe_path, resolve_search_path
from odoo_mcp.tools import rg as rg_tools
from odoo_mcp.tools.paths_util import FRAMEWORK_SCOPES, PATH_SCOPE_PREFIX, normalize_result_path

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


_TEST_EXCLUDES = ("!**/tests/**", "!**/test_*/**")
# Traductions (des centaines de lignes par module) et librairies JS vendorisées.
_NOISE_EXCLUDES = ("!**/i18n/**", "!**/i18n_extra/**", "!**/static/lib/**")


def _default_excludes(
    glob: str | None,
    file_type: str | None,
    scope: str,
    include_tests: bool,
    *,
    target_path: str = "",
    include_vendor: bool = False,
) -> tuple[list[str], list[str]]:
    """Globs d'exclusion par défaut + libellés pour la note, sauf si le glob/type/scope/chemin les cible explicitement."""
    glob_lower = (glob or "").lower()
    path_lower = target_path.lower()
    types = {t.strip().lower() for t in (file_type or "").split(",")}
    excludes: list[str] = []
    labels: list[str] = []
    targets_tests = "test" in glob_lower or "test" in path_lower or scope.lower().startswith("test_")
    if not include_tests and not targets_tests:
        excludes.extend(_TEST_EXCLUDES)
        labels.append("tests excluded (include_tests=True to search them)")
    if "i18n" not in glob_lower and "i18n" not in path_lower and "po" not in types:
        excludes.extend(_NOISE_EXCLUDES[:2])
        labels.append("i18n excluded")
    if not include_vendor and "static/lib" not in glob_lower and "static/lib" not in path_lower:
        excludes.append(_NOISE_EXCLUDES[2])
        labels.append("static/lib excluded (include_vendor=True or path=... to search vendored libs)")
    return excludes, labels


def search_code(
    paths: OdooPaths,
    pattern: str | None = None,
    *,
    query: str | None = None,
    scope: str = "all",
    path: str | None = None,
    glob: str | None = None,
    glob_pattern: str | None = None,
    file_type: str | None = None,
    context: int = 0,
    max_results: int = DEFAULT_MAX_RESULTS,
    case_insensitive: bool = False,
    fixed_string: bool = False,
    include_tests: bool = False,
    include_vendor: bool = False,
) -> str:
    effective_pattern = _coalesce(pattern, query)
    if not effective_pattern:
        return "Error: provide 'pattern' or 'query'."

    # Quote hint: Python source often uses single quotes; XML attributes use double quotes.
    # Pass query='name=\"foo\"' for XML doubles; query="name='foo'" for XML singles.

    effective_glob = _coalesce(glob_pattern, glob)
    effective_type = _coalesce(file_type)
    effective_path = _coalesce(path)
    scope_norm = (scope or "all").strip().lower()
    scope_labels: list[str] = []
    target_path = ""
    try:
        if effective_path:
            resolved = resolve_safe_path(paths, effective_path)
            search_paths = [resolved]
            target_path = normalize_result_path(resolved, paths)["relative_path"]
            scope_for_rg = f"{PATH_SCOPE_PREFIX}{effective_path}"
            scope_labels.append(f"path={target_path} (scope ignored)")
        else:
            search_paths = resolve_search_path(paths, scope)
            scope_for_rg = scope
            if scope_norm == "all":
                search_paths = [*search_paths, *paths.framework_roots]
                scope_labels.append("framework (odoo/*) included")
    except PathSecurityError as exc:
        return f"Error: {exc}"

    excludes, exclude_labels = _default_excludes(
        effective_glob,
        effective_type,
        scope_norm,
        include_tests,
        target_path=target_path,
        include_vendor=include_vendor,
    )
    if effective_path and search_paths[0].is_file():
        # Fichier ciblé explicitement : les exclusions par dossier n'ont pas de sens.
        excludes, exclude_labels = [], []
    matches, warning = rg_tools.run_rg(
        paths,
        effective_pattern,
        search_paths,
        scope=scope_for_rg,
        glob=effective_glob,
        file_type=effective_type,
        context=context,
        max_results=max_results,
        case_insensitive=case_insensitive,
        fixed_string=fixed_string,
        exclude_globs=excludes,
    )
    filters = [
        *scope_labels,
        f"file_type={effective_type}" if effective_type else "all file types",
        *exclude_labels,
    ]
    note = "Filters: " + ", ".join(filters)
    if not matches and not effective_path and scope_norm not in ("all", *FRAMEWORK_SCOPES):
        note += (
            "\nHint: framework code (odoo/orm, odoo/fields, odoo/models, odoo/tools…) is not in module/edition "
            "scopes — try scope='framework', or path='<dir or file>' to search a precise location."
        )
    return rg_tools.format_matches(
        matches,
        pattern=effective_pattern,
        max_results=max_results,
        warning=warning,
        note=note,
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
    try:
        file_path = resolve_safe_path(paths, path)
    except PathSecurityError as exc:
        return f"Error: {exc}"

    if not file_path.is_file():
        return f"Error: Not a file: {path!r}"

    info = normalize_result_path(file_path, paths)
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"Error: Cannot read {path!r}: {exc}"

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
