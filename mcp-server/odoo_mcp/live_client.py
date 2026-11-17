"""Client HTTP en lecture seule vers l'API externe d'Odoo (17/18 : /jsonrpc, 19+ : /json/2)."""

from __future__ import annotations

import itertools
import re
from typing import Any

import httpx

from odoo_mcp.live_config import InstanceProfile

# Seul point de passage vers l'instance : toute méthode absente de cette liste est refusée
# avant l'émission de la moindre requête.
ALLOWED_METHODS = frozenset(
    {"search_read", "read", "search_count", "fields_get", "read_group", "formatted_read_group"}
)

JSON2_MIN_VERSION = 19
_MODEL_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class OdooRpcError(Exception):
    """Erreur renvoyée par l'instance ou par le transport ; le message ne contient jamais la clé."""


class OdooClient:
    def __init__(self, profile: InstanceProfile, *, transport: httpx.BaseTransport | None = None) -> None:
        self.profile = profile
        # follow_redirects=False : la clé ne doit pas suivre une redirection vers un autre hôte.
        self._http = httpx.Client(timeout=_TIMEOUT, follow_redirects=False, transport=transport)
        self._version: int | None = None
        self._version_label: str | None = None
        self._uid: int | None = None
        self._ids = itertools.count(1)

    def close(self) -> None:
        self._http.close()

    # --- Version ---

    @property
    def version(self) -> int:
        """Version majeure de l'instance (détectée sans authentification, puis mémorisée)."""
        if self._version is None:
            info = self._jsonrpc_post("/web/webclient/version_info", {})
            label = str((info or {}).get("server_version") or "")
            match = re.search(r"(\d+)", label)
            if not match:
                raise OdooRpcError("could not detect the Odoo version of this instance")
            self._version = int(match.group(1))
            self._version_label = label
        return self._version

    @property
    def version_label(self) -> str:
        self.version  # noqa: B018 — déclenche la détection
        return self._version_label or ""

    # --- Appel de méthode (lecture seule) ---

    def call(self, model: str, method: str, *, ids: list[int] | None = None, **params: Any) -> Any:
        """Appelle une méthode ORM de la liste blanche. `ids` seulement pour les méthodes d'enregistrement."""
        if method not in ALLOWED_METHODS:
            raise OdooRpcError(f"method {method!r} is not allowed: this MCP is read-only")
        if not _MODEL_RE.match(model):
            raise OdooRpcError(f"invalid model name {model!r} (expected e.g. 'res.partner')")
        if self.version >= JSON2_MIN_VERSION:
            return self._call_json2(model, method, ids, params)
        return self._call_jsonrpc(model, method, ids, params)

    def _call_json2(self, model: str, method: str, ids: list[int] | None, params: dict[str, Any]) -> Any:
        body = dict(params)
        if ids is not None:
            body["ids"] = ids
        headers = {"Authorization": f"Bearer {self.profile.api_key}"}
        if self.profile.db:
            headers["X-Odoo-Database"] = self.profile.db
        response = self._post(f"/json/2/{model}/{method}", body, headers=headers)
        if response.status_code >= 400:
            raise OdooRpcError(self._scrub(_describe_http_error(response)))
        return self._json(response)

    def _call_jsonrpc(self, model: str, method: str, ids: list[int] | None, params: dict[str, Any]) -> Any:
        args = [ids] if ids is not None else []
        return self._execute("object", "execute_kw", [*self._credentials(), model, method, args, params])

    def _credentials(self) -> list[Any]:
        profile = self.profile
        if not profile.db or not profile.login:
            raise OdooRpcError(
                f"profile {profile.name!r} needs 'db' and 'login' in instances.json for Odoo {self.version} "
                "(only Odoo 19+ can authenticate with the API key alone)"
            )
        if self._uid is None:
            # Seule écriture côté instance : authenticate journalise la connexion (res.users.log).
            uid = self._execute("common", "authenticate", [profile.db, profile.login, profile.api_key, {}])
            if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
                raise OdooRpcError(
                    f"authentication failed for profile {profile.name!r}: check db, login and api_key in instances.json"
                )
            self._uid = uid
        return [profile.db, self._uid, profile.api_key]

    def _execute(self, service: str, method: str, args: list[Any]) -> Any:
        return self._jsonrpc_post("/jsonrpc", {"service": service, "method": method, "args": args})

    # --- Transport ---

    def _jsonrpc_post(self, path: str, params: dict[str, Any]) -> Any:
        payload = {"jsonrpc": "2.0", "method": "call", "params": params, "id": next(self._ids)}
        response = self._post(path, payload)
        if response.status_code >= 400:
            raise OdooRpcError(self._scrub(_describe_http_error(response)))
        data = self._json(response)
        if not isinstance(data, dict):
            raise OdooRpcError("unexpected JSON-RPC response from the instance")
        if data.get("error"):
            raise OdooRpcError(self._scrub(_describe_odoo_error(data["error"])))
        return data.get("result")

    def _post(self, path: str, body: dict[str, Any], *, headers: dict[str, str] | None = None) -> httpx.Response:
        try:
            response = self._http.post(self.profile.url + path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            # str(exc) peut reprendre l'URL, jamais les en-têtes ni le corps ; on masque quand même.
            raise OdooRpcError(self._scrub(f"cannot reach {self.profile.url}: {type(exc).__name__}: {exc}")) from None
        if 300 <= response.status_code < 400:
            raise OdooRpcError(
                f"{self.profile.url} answered with a redirect (HTTP {response.status_code}), which is not followed. "
                "Check the 'url' of this profile in instances.json."
            )
        return response

    def _json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            raise OdooRpcError(
                f"{self.profile.url} did not answer with JSON (HTTP {response.status_code}); is it an Odoo instance?"
            ) from None

    def _scrub(self, message: str) -> str:
        return message.replace(self.profile.api_key, "***")


def _describe_odoo_error(error: object) -> str:
    """Réduit une erreur Odoo à « nom: message » (sans la trace `debug`)."""
    if not isinstance(error, dict):
        return "Odoo error"
    data = error.get("data") if isinstance(error.get("data"), dict) else error
    name = str(data.get("name") or "").rsplit(".", 1)[-1]
    message = str(data.get("message") or error.get("message") or "Odoo error").strip()
    return f"{name}: {message}" if name else message


def _describe_http_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = _describe_odoo_error(body) if isinstance(body, dict) else ""
    hint = ""
    if response.status_code == 401:
        hint = " (API key rejected: check api_key, its expiration date, and 'db' in instances.json)"
    return f"HTTP {response.status_code}{': ' + detail if detail else ''}{hint}"
