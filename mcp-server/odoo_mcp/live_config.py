"""Profils d'instances Odoo pour le MCP « live » (fichier relu à chaud, hors dépôt)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_INSTANCES_FILE = "/config/instances.json"

# Hôtes pour lesquels http (sans TLS) est toléré : la clé ne quitte pas la machine.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_KNOWN_KEYS = frozenset({"url", "db", "login", "api_key"})


class ProfileError(ValueError):
    """Fichier de profils absent ou invalide, ou profil inconnu."""


@dataclass(frozen=True)
class InstanceProfile:
    name: str
    url: str
    api_key: str = field(repr=False)  # jamais dans un repr, donc jamais dans un log
    db: str | None = None
    login: str | None = None


@dataclass(frozen=True)
class ProfileSet:
    profiles: dict[str, InstanceProfile]
    errors: dict[str, str]  # profils rejetés : nom -> raison


def instances_file() -> Path:
    return Path(os.environ.get("ODOO_INSTANCES_FILE", DEFAULT_INSTANCES_FILE))


def _validate_url(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ProfileError("'url' is required")
    parts = urlsplit(raw.strip())
    host = (parts.hostname or "").lower()
    if not host:
        raise ProfileError("'url' has no host")
    if parts.username or parts.password:
        raise ProfileError("'url' must not contain credentials")
    if parts.query or parts.fragment:
        raise ProfileError("'url' must not contain a query string or fragment")
    if parts.scheme == "http":
        if host not in _LOCAL_HOSTS:
            raise ProfileError("'url' must use https (http is only accepted for localhost / host.docker.internal)")
    elif parts.scheme != "https":
        raise ProfileError("'url' must start with https://")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def _optional_str(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ProfileError(f"{key!r} must be a string")
    return value.strip()


def parse_profile(name: str, data: object) -> InstanceProfile:
    if not _NAME_RE.match(name):
        raise ProfileError("profile name may only contain letters, digits, '_', '-' and '.'")
    if not isinstance(data, dict):
        raise ProfileError("profile must be a JSON object")
    unknown = sorted(set(data) - _KNOWN_KEYS)
    if unknown:
        raise ProfileError(f"unknown key(s): {', '.join(unknown)} (allowed: url, db, login, api_key)")
    api_key = data.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ProfileError("'api_key' is required")
    return InstanceProfile(
        name=name,
        url=_validate_url(data.get("url")),
        api_key=api_key.strip(),
        db=_optional_str(data, "db"),
        login=_optional_str(data, "login"),
    )


_cache: tuple[tuple[str, int, int], ProfileSet] | None = None


def load_profiles(path: Path | None = None) -> ProfileSet:
    """Charge le fichier de profils ; relu seulement si sa date ou sa taille a changé."""
    global _cache
    path = path or instances_file()
    try:
        stat = path.stat()
    except OSError:
        raise ProfileError(
            f"instances file not found at {path}. Create instances.json in the mounted config folder "
            "(see instances.example.json)."
        ) from None
    stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    if _cache is not None and _cache[0] == stamp:
        return _cache[1]

    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        # Pas d'extrait du contenu : le fichier contient des clés API.
        raise ProfileError(f"instances file is not valid JSON (line {exc.lineno}, column {exc.colno})") from None
    except OSError as exc:
        raise ProfileError(f"cannot read instances file: {exc.strerror}") from None
    if not isinstance(raw, dict):
        raise ProfileError("instances file must be a JSON object: {\"<profile name>\": {...}}")

    profiles: dict[str, InstanceProfile] = {}
    errors: dict[str, str] = {}
    for name, data in raw.items():
        try:
            profiles[name] = parse_profile(name, data)
        except ProfileError as exc:
            errors[name] = str(exc)
    result = ProfileSet(profiles=profiles, errors=errors)
    _cache = (stamp, result)
    return result


def get_profile(name: str, path: Path | None = None) -> InstanceProfile:
    loaded = load_profiles(path)
    if name in loaded.profiles:
        return loaded.profiles[name]
    if name in loaded.errors:
        raise ProfileError(f"profile {name!r} is invalid: {loaded.errors[name]}")
    available = ", ".join(sorted(loaded.profiles)) or "none"
    raise ProfileError(f"unknown instance {name!r}. Available: {available}. Call list_instances.")
