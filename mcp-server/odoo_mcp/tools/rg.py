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
            paths,
            pattern,
            search_paths,
            glob=glob,
            max_results=max_results,
            fixed_string=fixed_string,
            patterns=patterns,
            case_insensitive=case_insensitive,
            file_type=file_type,
            exclude_globs=exclude_globs,
            context=context,
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

    matches = _parse_rg_json(result.stdout, paths, max_results, context)
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


def _parse_rg_json(stdout: str, paths: OdooPaths, max_results: int, context: int = 0) -> list[RgMatch]:
    """Parse la sortie `rg --json`.

    rg émet chaque ligne de contexte une seule fois, même quand elle est partagée entre deux
    matches proches. Une ligne de contexte qui suit directement le dernier match (à `context`
    lignes au plus) devient son contexte « après » ; les autres deviennent le contexte « avant »
    du match suivant. Les deux blocs restent ainsi contigus, ce qui permet au formateur de
    recalculer des numéros de ligne exacts.
    """
    pending_before: list[str] = []
    results: list[RgMatch] = []
    last: RgMatch | None = None  # dernier match du fichier en cours

    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type")
        data = entry.get("data", {})
        line_text = data.get("lines", {}).get("text", "").rstrip("\r\n")

        if entry_type == "context":
            line_no = data.get("line_number") or 0
            if (
                last is not None
                and not pending_before
                and line_no == last.line + len(last.context_after) + 1
                and line_no - last.line <= context
            ):
                last.context_after.append(line_text)
            else:
                pending_before.append(line_text)
            continue

        if entry_type != "match":
            # begin / end / summary : changement de fichier.
            if len(results) >= max_results:
                break
            pending_before = []
            last = None
            continue

        if len(results) >= max_results:
            # Limite atteinte : on ne garde que le contexte « après » du dernier match.
            last = None
            continue

        path_info = normalize_result_path(data["path"]["text"], paths)
        last = RgMatch(
            file=path_info["file"],
            line=data["line_number"],
            content=line_text,
            relative_path=path_info["relative_path"],
            edition=path_info["edition"],
            workspace_hint=path_info["workspace_hint"],
            context_before=pending_before,
            context_after=[],
        )
        pending_before = []
        results.append(last)

    return results


# Sous-ensemble des types ripgrep (`rg --type-list`) utiles sur une codebase Odoo.
_RG_TYPES: dict[str, tuple[str, ...]] = {
    "py": ("*.py", "*.pyi"),
    "python": ("*.py", "*.pyi"),
    "xml": ("*.xml", "*.xsd", "*.xsl", "*.xslt", "*.rng", "*.svg"),
    "js": ("*.js", "*.jsx", "*.mjs", "*.cjs", "*.vue"),
    "ts": ("*.ts", "*.tsx", "*.mts", "*.cts"),
    "css": ("*.css", "*.scss"),
    "scss": ("*.scss",),
    "html": ("*.html", "*.htm", "*.xhtml"),
    "json": ("*.json",),
    "csv": ("*.csv",),
    "po": ("*.po",),
    "md": ("*.md", "*.markdown"),
    "markdown": ("*.md", "*.markdown"),
    "txt": ("*.txt",),
    "sql": ("*.sql",),
    "yaml": ("*.yaml", "*.yml"),
}
_SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules"})


def _glob_to_regex(glob: str) -> re.Pattern[str]:
    """Traduit un glob ripgrep (**, *, ?, {a,b}, [..]) en regex ancrée sur un chemin POSIX relatif."""
    out: list[str] = []
    i = 0
    n = len(glob)
    while i < n:
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("/**", i) and i + 3 == n:
            out.append("(?:/.*)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        elif glob[i] == "{":
            end = glob.find("}", i)
            if end == -1:
                out.append(re.escape(glob[i]))
                i += 1
                continue
            alts = glob[i + 1 : end].split(",")
            out.append("(?:" + "|".join(_glob_to_regex(a).pattern[:-2] for a in alts) + ")")
            i = end + 1
        elif glob[i] == "[":
            end = glob.find("]", i + 1)
            if end == -1:
                out.append(re.escape(glob[i]))
                i += 1
                continue
            body = glob[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append(f"[{body}]")
            i = end + 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _compile_globs(
    glob: str | None, exclude_globs: list[str] | None, file_type: str | None
) -> tuple[list[tuple[bool, bool, re.Pattern[str]]], list[re.Pattern[str]]]:
    """Globs dans l'ordre rg (le dernier qui matche l'emporte) + filtres de type de fichier."""
    rules: list[tuple[bool, bool, re.Pattern[str]]] = []  # (include, basename_only, regex)
    for raw in ("!.git/**", "!**/__pycache__/**", "!**/node_modules/**", glob, *(exclude_globs or ())):
        if not raw:
            continue
        include = not raw.startswith("!")
        text = raw if include else raw[1:]
        rules.append((include, "/" not in text, _glob_to_regex(text)))
    type_patterns: list[re.Pattern[str]] = []
    for type_name in (file_type or "").split(","):
        for type_glob in _RG_TYPES.get(type_name.strip().lower(), ()):
            type_patterns.append(_glob_to_regex(type_glob))
    return rules, type_patterns


def _path_selected(
    rel: str,
    rules: list[tuple[bool, bool, re.Pattern[str]]],
    type_patterns: list[re.Pattern[str]],
) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if type_patterns and not any(p.match(name) for p in type_patterns):
        return False
    decision: bool | None = None
    for include, basename_only, regex in rules:
        if regex.match(name if basename_only else rel):
            decision = include
    if decision is None:
        # rg : dès qu'un glob positif existe, un fichier qui n'en matche aucun est ignoré.
        return not any(include for include, _, _ in rules)
    return decision


def _python_scan(
    paths: OdooPaths,
    pattern: str,
    search_paths: list[Path],
    *,
    glob: str | None,
    max_results: int,
    fixed_string: bool,
    patterns: list[str] | None = None,
    case_insensitive: bool = False,
    file_type: str | None = None,
    exclude_globs: list[str] | None = None,
    context: int = 0,
) -> list[RgMatch]:
    """Repli sans ripgrep : mêmes options que `run_rg` (patterns, -i, -F, globs, types, contexte)."""
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regexes = [
            re.compile(re.escape(p) if fixed_string else p, flags) for p in (patterns or [pattern])
        ]
    except re.error:
        return []

    rules, type_patterns = _compile_globs(glob, exclude_globs, file_type)
    results: list[RgMatch] = []

    def _scan_file(path: Path) -> None:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        hits = [idx for idx, line in enumerate(lines) if any(r.search(line) for r in regexes)]
        if not hits:
            return
        info = normalize_result_path(path, paths)
        prev_end = -1  # dernière ligne (index 0) déjà affichée pour ce fichier
        for pos, idx in enumerate(hits):
            if len(results) >= max_results:
                return
            next_hit = hits[pos + 1] if pos + 1 < len(hits) else len(lines)
            before_start = max(idx - context, prev_end + 1)
            after_end = min(idx + context, next_hit - 1, len(lines) - 1)
            results.append(
                RgMatch(
                    file=info["file"],
                    line=idx + 1,
                    content=lines[idx].rstrip(),
                    relative_path=info["relative_path"],
                    edition=info["edition"],
                    workspace_hint=info["workspace_hint"],
                    context_before=[l.rstrip() for l in lines[before_start:idx]] if context else [],
                    context_after=[l.rstrip() for l in lines[idx + 1 : after_end + 1]] if context else [],
                )
            )
            prev_end = after_end if context else idx

    for root in search_paths:
        if len(results) >= max_results:
            break
        if root.is_file():
            # Fichier passé explicitement : rg le cherche toujours, globs ou pas.
            _scan_file(root)
            continue
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if len(results) >= max_results:
                break
            if not path.is_file():
                continue
            rel_parts = path.relative_to(root).parts
            if any(part in _SKIP_DIRS for part in rel_parts):
                continue
            if not _path_selected("/".join(rel_parts), rules, type_patterns):
                continue
            _scan_file(path)
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
        has_context = any(m.context_before or m.context_after for m in matches)
        prev: tuple[str, int] | None = None  # (fichier, dernière ligne affichée)
        for m in matches:
            first_line = m.line - len(m.context_before)
            # Séparateur « -- » entre blocs non contigus (comme rg), seulement en mode contexte.
            if has_context and prev is not None and (prev[0] != m.relative_path or first_line > prev[1] + 1):
                lines.append("--")
            for i, ctx in enumerate(m.context_before):
                lines.append(f"{m.relative_path}:{first_line + i}- {ctx}")
            lines.append(f"{m.relative_path}:{m.line}: {m.content}")
            for i, ctx in enumerate(m.context_after, 1):
                lines.append(f"{m.relative_path}:{m.line + i}- {ctx}")
            prev = (m.relative_path, m.line + len(m.context_after))
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
