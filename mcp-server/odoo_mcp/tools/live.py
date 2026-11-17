"""Outils d'exploration en lecture seule d'une instance Odoo (MCP « live »)."""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Callable
from typing import Any, ParamSpec

from odoo_mcp.live_client import JSON2_MIN_VERSION, OdooClient, OdooRpcError
from odoo_mcp.live_config import InstanceProfile, ProfileError, get_profile, load_profiles

P = ParamSpec("P")

MAX_LIMIT = 200
MAX_IDS = 200
MAX_OUTPUT_CHARS = 40_000
ALL_FIELDS = "*"

_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GROUP_SPEC_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*(:[a-z_]+)?$")
_ORDER_RE = re.compile(r"^[A-Za-z0-9_.]+( (asc|desc))?( nulls (first|last))?$", re.IGNORECASE)
_FIELD_ATTRIBUTES = ["string", "type", "relation", "relation_field", "required", "readonly", "store", "selection"]

# Un client par profil : garde la version détectée et l'uid. Recréé dès que le profil change.
_clients: dict[str, OdooClient] = {}


class InputError(ValueError):
    """Argument d'outil invalide."""


def _client(instance: str) -> OdooClient:
    profile = get_profile(instance)
    return _client_for(profile)


def _client_for(profile: InstanceProfile) -> OdooClient:
    cached = _clients.get(profile.name)
    if cached is not None and cached.profile == profile:
        return cached
    if cached is not None:
        cached.close()
    _clients[profile.name] = OdooClient(profile)
    return _clients[profile.name]


def _tool(func: Callable[P, str]) -> Callable[P, str]:
    """Convertit les erreurs attendues en texte « Error: … » lisible par le modèle."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> str:
        try:
            return func(*args, **kwargs)
        except (ProfileError, OdooRpcError, InputError) as exc:
            return f"Error: {exc}"

    return wrapper


# --- Validation des entrées ---


def _domain(value: object) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise InputError("domain must be a JSON list, e.g. [[\"is_company\", \"=\", true]]") from None
    if not isinstance(value, list):
        raise InputError("domain must be a list, e.g. [[\"is_company\", \"=\", true]]")
    return value


def _fields(value: object) -> list[str] | None:
    """None = tous les champs (demandé par ['*']) ; sinon la liste validée."""
    if not value:
        return ["display_name"]
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, list):
        raise InputError("fields must be a list of field names")
    if ALL_FIELDS in value:
        return None
    for name in value:
        if not isinstance(name, str) or not _FIELD_RE.match(name):
            raise InputError(f"invalid field name {name!r} (dotted paths are not supported: read the related model)")
    return value


def _limit(value: int) -> int:
    return max(1, min(int(value), MAX_LIMIT))


def _context(include_archived: bool) -> dict[str, Any]:
    # Contexte imposé ici, jamais fourni par l'appelant. bin_size : taille au lieu du contenu binaire.
    return {"bin_size": True, "active_test": not include_archived}


# --- Mise en forme ---


def _render(header: dict[str, Any], rows: list[Any]) -> str:
    """Une ligne d'en-tête JSON puis un enregistrement JSON par ligne ; coupe sur une ligne entière."""
    lines = [json.dumps(row, ensure_ascii=False, default=str) for row in rows]
    kept: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > MAX_OUTPUT_CHARS:
            break
        kept.append(line)
        size += len(line) + 1
    if len(kept) < len(lines):
        header = {
            **header,
            "output_truncated": f"{len(lines) - len(kept)} of {len(lines)} rows omitted (output too large): "
            "request fewer fields or a smaller limit",
        }
    return "\n".join([json.dumps(header, ensure_ascii=False, default=str), *kept])


# --- Outils ---


@_tool
def list_instances() -> str:
    loaded = load_profiles()
    rows: list[dict[str, Any]] = []
    for name in sorted(loaded.profiles):
        profile = loaded.profiles[name]
        row: dict[str, Any] = {"instance": name, "url": profile.url, "db": profile.db, "login": profile.login}
        try:
            row["version"] = _client_for(profile).version_label
        except OdooRpcError as exc:
            row["unreachable"] = str(exc)
        rows.append(row)
    for name, reason in sorted(loaded.errors.items()):
        rows.append({"instance": name, "invalid": reason})
    return _render({"instances": len(loaded.profiles), "invalid": len(loaded.errors)}, rows)


@_tool
def list_models(instance: str, query: str = "", limit: int = 50) -> str:
    domain: list = ["|", ["model", "ilike", query], ["name", "ilike", query]] if query else []
    limit = _limit(limit)
    try:
        rows = _client(instance).call(
            "ir.model",
            "search_read",
            domain=domain,
            fields=["model", "name", "transient"],
            limit=limit + 1,
            order="model",
            context=_context(False),
        )
    except OdooRpcError as exc:
        if "AccessError" in str(exc):
            raise OdooRpcError(
                f"{exc} — listing models needs administration rights on this instance. "
                "If you know the model name, call get_fields directly."
            ) from None
        raise
    has_more = len(rows) > limit
    rows = [{"model": r["model"], "name": r["name"], **({"transient": True} if r.get("transient") else {})} for r in rows[:limit]]
    return _render({"instance": instance, "returned": len(rows), "has_more": has_more}, rows)


@_tool
def get_fields(instance: str, model: str, query: str = "", fields: list[str] | None = None) -> str:
    wanted = _fields(fields) if fields else None
    result = _client(instance).call(
        model, "fields_get", allfields=wanted, attributes=_FIELD_ATTRIBUTES, context=_context(False)
    )
    needle = query.lower()
    rows = []
    for name in sorted(result):
        attrs = result[name]
        if needle and needle not in name.lower() and needle not in str(attrs.get("string", "")).lower():
            continue
        # Les attributs faux ou vides sont omis pour alléger la sortie.
        rows.append({"field": name, **{k: v for k, v in attrs.items() if k in _FIELD_ATTRIBUTES and v not in (False, None, "", [])}})
    return _render({"instance": instance, "model": model, "fields": len(rows), "total": len(result)}, rows)


@_tool
def search_records(
    instance: str,
    model: str,
    domain: object = None,
    fields: object = None,
    limit: int = 20,
    offset: int = 0,
    order: str | None = None,
    include_archived: bool = False,
) -> str:
    limit = _limit(limit)
    offset = max(0, int(offset))
    if order:
        for part in order.split(","):
            if not _ORDER_RE.match(part.strip()):
                raise InputError(f"invalid order {order!r} (expected e.g. 'create_date desc, id')")
    # Un enregistrement de plus que demandé, pour savoir s'il en reste.
    rows = _client(instance).call(
        model,
        "search_read",
        domain=_domain(domain),
        fields=_fields(fields),
        offset=offset,
        limit=limit + 1,
        order=order or None,
        context=_context(include_archived),
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return _render(
        {"instance": instance, "model": model, "returned": len(rows), "offset": offset, "has_more": has_more}, rows
    )


@_tool
def read_records(instance: str, model: str, ids: list[int], fields: object = None) -> str:
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise InputError("ids must be a non-empty list of integers")
    if len(ids) > MAX_IDS:
        raise InputError(f"at most {MAX_IDS} ids per call")
    rows = _client(instance).call(model, "read", ids=ids, fields=_fields(fields), context=_context(True))
    return _render({"instance": instance, "model": model, "returned": len(rows)}, rows)


@_tool
def count_records(instance: str, model: str, domain: object = None, include_archived: bool = False) -> str:
    count = _client(instance).call(
        model, "search_count", domain=_domain(domain), context=_context(include_archived)
    )
    return json.dumps({"instance": instance, "model": model, "count": count})


@_tool
def group_records(
    instance: str,
    model: str,
    groupby: list[str],
    domain: object = None,
    aggregates: list[str] | None = None,
    limit: int = 80,
    include_archived: bool = False,
) -> str:
    if not isinstance(groupby, list) or not groupby:
        raise InputError("groupby must be a non-empty list, e.g. ['state'] or ['create_date:month']")
    aggregates = [a for a in (aggregates or []) if a != "__count"]
    for spec in [*groupby, *aggregates]:
        if not isinstance(spec, str) or not _GROUP_SPEC_RE.match(spec):
            raise InputError(f"invalid spec {spec!r} (expected 'field', 'date_field:month' or 'field:sum')")
    client = _client(instance)
    common = {"domain": _domain(domain), "groupby": groupby, "limit": _limit(limit), "context": _context(include_archived)}
    if client.version >= JSON2_MIN_VERSION:
        rows = client.call(model, "formatted_read_group", aggregates=[*aggregates, "__count"], **common)
    else:
        # read_group (17/18) : les agrégats passent par `fields`, le compte (__count) est toujours renvoyé.
        rows = client.call(model, "read_group", fields=aggregates, lazy=False, **common)
    rows = [{k: v for k, v in row.items() if k not in ("__domain", "__extra_domain", "__context")} for row in rows]
    return _render({"instance": instance, "model": model, "groups": len(rows)}, rows)
