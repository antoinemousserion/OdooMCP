"""Wrapper ripgrep avec contexte, chemins normalisés et mode fixed-string."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from odoo_mcp.config import OdooPaths
from odoo_mcp.tools.paths_util import enterprise_warning, normalize_result_path

RG_TIMEOUT = 60
LOGGER = logging.getLogger("odoo_mcp.rg")


@dataclass
class RgMatch:
    file: str
    line: int
    content: str
    relative_path: str
    edition: str
    workspace_hint: str
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


def escape_regex_literal(text: str) -> str:
    return re.escape(text)


def run_rg(
    paths: OdooPaths,
    pattern: str,
    search_paths: list[Path],
    *,
    scope: str = "all",
    glob: str | None = None,
    file_type: str | None = "py",
    context: int = 0,
    max_results: int = 100,
    case_insensitive: bool = False,
    fixed_string: bool = False,
    patterns: list[str] | None = None,
    exclude_globs: list[str] | None = None,
) -> tuple[list[RgMatch], str | None]:
    warning = enterprise_warning(paths, scope)

    if not search_paths:
        return [], warning

    if not shutil.which("rg"):
        return _python_scan(
            paths, pattern, search_paths, glob=glob, max_results=max_results, fixed_string=fixed_string
        ), warning

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
    for exclude in ("!.git/**", "!**/__pycache__/**", "!**/node_modules/**"):
        cmd.extend(["--glob", exclude])
    if glob:
        cmd.extend(["--glob", glob])
    # Après le glob utilisateur : dans rg, le dernier glob qui matche l'emporte.
    for exclude in exclude_globs or ():
        cmd.extend(["--glob", exclude])
    if file_type:
        for type_name in file_type.split(","):
            if type_name.strip():
                cmd.extend(["--type", type_name.strip()])
    if fixed_string:
        cmd.append("-F")
    search_patterns = patterns if patterns else [pattern]
    for search_pattern in search_patterns:
        cmd.extend(["-e", search_pattern])
    cmd.append("--")
    cmd.extend(str(p) for p in search_paths if p.is_dir() or p.is_file())

    start = time.perf_counter()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=RG_TIMEOUT)
    except subprocess.TimeoutExpired:
        LOGGER.warning(
            "rg timeout pattern=%r scope=%s paths=%d glob=%s fixed=%s after %dms",
            pattern[:120],
            scope,
            len(search_paths),
            glob,
            fixed_string,
            RG_TIMEOUT * 1000,
        )
        return [], warning

    elapsed_ms = (time.perf_counter() - start) * 1000

    if result.returncode not in (0, 1):
        LOGGER.warning(
            "rg error rc=%s pattern=%r scope=%s duration_ms=%.1f stderr=%s",
            result.returncode,
            pattern[:120],
            scope,
            elapsed_ms,
            (result.stderr or "").strip()[:200],
        )
        # Remonter l'erreur (ex. type inconnu) plutôt qu'un faux « No results ».
        error = f"ripgrep error: {(result.stderr or '').strip()[:300]}"
        return [], f"{warning}\n\n{error}" if warning else error

    matches = _parse_rg_json(result.stdout, paths, max_results)
    LOGGER.info(
        "rg pattern=%r scope=%s paths=%d glob=%s type=%s fixed=%s matches=%d duration_ms=%.1f",
        (search_patterns[0] if len(search_patterns) == 1 else f"{len(search_patterns)} patterns")[:120],
        scope,
        len(search_paths),
        glob,
        file_type,
        fixed_string,
        len(matches),
        elapsed_ms,
    )
    return matches, warning


def _parse_rg_json(stdout: str, paths: OdooPaths, max_results: int) -> list[RgMatch]:
    pending_before: list[str] = []
    results: list[RgMatch] = []
    current: RgMatch | None = None

    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type")
        data = entry.get("data", {})
        line_text = data.get("lines", {}).get("text", "").rstrip("\n")

        if entry_type == "context":
            if current is not None:
                current.context_after.append(line_text)
            else:
                pending_before.append(line_text)
            continue

        if entry_type != "match":
            pending_before = []
            continue

        path_info = normalize_result_path(data["path"]["text"], paths)
        current = RgMatch(
            file=path_info["file"],
            line=data["line_number"],
            content=line_text,
            relative_path=path_info["relative_path"],
            edition=path_info["edition"],
            workspace_hint=path_info["workspace_hint"],
            context_before=pending_before.copy(),
            context_after=[],
        )
        pending_before = []
        results.append(current)
        current = None

        if len(results) >= max_results:
            break

    return results


def _python_scan(
    paths: OdooPaths,
    pattern: str,
    search_paths: list[Path],
    *,
    glob: str | None,
    max_results: int,
    fixed_string: bool,
) -> list[RgMatch]:
    try:
        regex = re.compile(re.escape(pattern) if fixed_string else pattern)
    except re.error:
        return []

    suffix = None
    if glob and glob.startswith("*."):
        suffix = glob[1:]

    results: list[RgMatch] = []

    def _scan_file(path: Path) -> None:
        nonlocal results
        if len(results) >= max_results:
            return
        if suffix and not str(path).endswith(suffix):
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        for idx, line in enumerate(lines, 1):
            if len(results) >= max_results:
                return
            if fixed_string:
                found = pattern in line
            else:
                found = bool(regex.search(line))
            if not found:
                continue
            info = normalize_result_path(path, paths)
            results.append(
                RgMatch(
                    file=info["file"],
                    line=idx,
                    content=line.rstrip(),
                    relative_path=info["relative_path"],
                    edition=info["edition"],
                    workspace_hint=info["workspace_hint"],
                )
            )

    for root in search_paths:
        if root.is_file():
            _scan_file(root)
            if len(results) >= max_results:
                return results
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            _scan_file(path)
            if len(results) >= max_results:
                return results
    return results


def format_matches(
    matches: list[RgMatch],
    *,
    pattern: str,
    max_results: int,
    warning: str | None = None,
    note: str | None = None,
    as_json: bool = False,
) -> str:
    if not matches:
        msg = f"No results for {pattern!r}."
        if note:
            msg = f"{msg}\n{note}"
        if warning:
            msg = f"{warning}\n\n{msg}"
        return msg

    if as_json:
        import json as json_mod

        payload: dict = {
            "count": len(matches),
            "results": [
                {
                    "file": m.file,
                    "relative_path": m.relative_path,
                    "workspace_hint": m.workspace_hint,
                    "edition": m.edition,
                    "line": m.line,
                    "content": m.content,
                    "context_before": m.context_before,
                    "context_after": m.context_after,
                }
                for m in matches
            ],
        }
        if warning:
            payload["warning"] = warning
        text = json_mod.dumps(payload, indent=2, ensure_ascii=False)
    else:
        lines: list[str] = []
        for m in matches:
            if m.context_before:
                for ctx in m.context_before:
                    lines.append(f"{m.relative_path}:{m.line - len(m.context_before)}- {ctx}")
            lines.append(f"{m.relative_path}:{m.line}: {m.content}")
            for i, ctx in enumerate(m.context_after, 1):
                lines.append(f"{m.relative_path}:{m.line + i}- {ctx}")
        header = f"{len(matches)} result(s) (max {max_results})"
        if len(matches) >= max_results:
            header += " — limit reached: narrow scope/glob or raise max_results"
        if note:
            header += f"\n{note}"
        text = header + "\n\n" + "\n".join(lines)
        if warning:
            text = f"{warning}\n\n{text}"

    from odoo_mcp.tools.search import _truncate

    return _truncate(text)
