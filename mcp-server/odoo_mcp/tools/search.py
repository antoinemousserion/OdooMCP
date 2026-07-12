"""Outils de recherche ripgrep/glob optimisés pour les grosses codebases."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.security import PathSecurityError, resolve_safe_path, resolve_search_path

MAX_OUTPUT_CHARS = 80_000
DEFAULT_MAX_RESULTS = 100
RG_TIMEOUT = 60


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... [tronqué — {len(text) - limit} caractères omis]"


def _run_rg(
    pattern: str,
    search_paths: list[Path],
    *,
    glob: str | None = None,
    file_type: str | None = None,
    context: int = 0,
    max_results: int = DEFAULT_MAX_RESULTS,
    case_insensitive: bool = False,
) -> str:
    if not shutil.which("rg"):
        return _grep_fallback(pattern, search_paths, glob=glob, max_results=max_results)

    cmd = [
        "rg",
        "--json",
        "--max-count",
        str(max_results),
        "--max-columns",
        "500",
    ]
    if case_insensitive:
        cmd.append("-i")
    if context:
        cmd.extend(["-C", str(context)])
    if glob:
        cmd.extend(["--glob", glob])
    if file_type:
        cmd.extend(["--type", file_type])
    cmd.extend(["--", pattern])
    cmd.extend(str(p) for p in search_paths if p.is_dir())

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=RG_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "Erreur : recherche expirée (timeout 60s). Affinez le pattern ou réduisez le scope."

    lines = result.stdout.strip().splitlines()
    matches: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "match":
            continue
        data = entry["data"]
        path_text = data["path"]["text"]
        line_num = data["line_number"]
        line_text = data["lines"]["text"].rstrip("\n")
        matches.append({"file": path_text, "line": line_num, "content": line_text})
        if len(matches) >= max_results:
            break

    if not matches:
        hint = ""
        if result.returncode not in (0, 1):
            hint = f"\nstderr: {result.stderr.strip()}"
        return f"Aucun résultat pour {pattern!r}.{hint}"

    output_lines = [f"{m['file']}:{m['line']}: {m['content']}" for m in matches]
    header = f"{len(matches)} résultat(s) (max {max_results})\n\n"
    return _truncate(header + "\n".join(output_lines))


def _grep_fallback(
    pattern: str,
    search_paths: list[Path],
    *,
    glob: str | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> str:
    """Fallback Python si ripgrep absent (dev local sans Docker)."""
    import re

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"Pattern regex invalide : {exc}"

    matches: list[str] = []
    suffix = glob.replace("*", "") if glob and glob.startswith("*.") else None

    for root in search_paths:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if suffix and path.suffix != suffix:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{path}:{i}: {line.rstrip()}")
                    if len(matches) >= max_results:
                        return _truncate(f"{len(matches)} résultat(s)\n\n" + "\n".join(matches))

    if not matches:
        return f"Aucun résultat pour {pattern!r} (fallback Python, ripgrep absent)."
    return _truncate(f"{len(matches)} résultat(s)\n\n" + "\n".join(matches))


def search_code(
    paths: OdooPaths,
    pattern: str,
    *,
    scope: str = "all",
    glob: str | None = None,
    file_type: str | None = "py",
    context: int = 0,
    max_results: int = DEFAULT_MAX_RESULTS,
    case_insensitive: bool = False,
) -> str:
    search_paths = resolve_search_path(paths, scope)
    if not search_paths:
        return f"Aucun répertoire disponible pour le scope {scope!r}."
    return _run_rg(
        pattern,
        search_paths,
        glob=glob,
        file_type=file_type,
        context=context,
        max_results=max_results,
        case_insensitive=case_insensitive,
    )


def glob_files(
    paths: OdooPaths,
    pattern: str,
    *,
    scope: str = "all",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> str:
    search_paths = resolve_search_path(paths, scope)
    if not search_paths:
        return f"Aucun répertoire disponible pour le scope {scope!r}."

    found: list[str] = []
    for root in search_paths:
        for match in root.glob(f"**/{pattern}" if not pattern.startswith("**/") else pattern):
            if match.is_file():
                found.append(str(match))
                if len(found) >= max_results:
                    break
        if len(found) >= max_results:
            break

    if not found:
        return f"Aucun fichier pour le pattern {pattern!r}."
    return _truncate(f"{len(found)} fichier(s)\n\n" + "\n".join(sorted(found)))


def read_file(
    paths: OdooPaths,
    path: str,
    *,
    offset: int = 1,
    limit: int = 200,
) -> str:
    file_path = resolve_safe_path(paths, path)
    if not file_path.is_file():
        raise PathSecurityError(f"N'est pas un fichier : {path}")

    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    start = max(0, offset - 1)
    end = min(total, start + limit)
    selected = lines[start:end]

    numbered = [f"{i + start + 1:6}| {line}" for i, line in enumerate(selected)]
    header = f"Fichier: {file_path}\nLignes {start + 1}-{end} sur {total}\n\n"
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
            return "Aucune codebase Odoo montée. Vérifiez le clone Git."
        return "\n".join(entries)

    dir_path = resolve_safe_path(paths, path)
    if not dir_path.is_dir():
        raise PathSecurityError(f"N'est pas un répertoire : {path}")

    items: list[str] = []
    for entry in sorted(dir_path.iterdir()):
        suffix = "/" if entry.is_dir() else ""
        items.append(entry.name + suffix)
        if len(items) >= max_entries:
            items.append(f"... [{dir_path} contient plus de {max_entries} entrées]")
            break
    return f"{dir_path}/\n\n" + "\n".join(items)
