"""Tests du MCP « live » contre une fausse instance Odoo (httpx.MockTransport)."""

from __future__ import annotations

import json
import os

import httpx
import pytest

from odoo_mcp import live_config
from odoo_mcp.live_client import ALLOWED_METHODS, OdooClient, OdooRpcError
from odoo_mcp.live_config import InstanceProfile, ProfileError, get_profile, load_profiles, parse_profile
from odoo_mcp.tools import live as live_tools

KEY = "s3cr3t-api-key"


class FakeOdoo:
    """Répond comme Odoo 17/18 (/jsonrpc) ou 19 (/json/2) et enregistre les requêtes reçues."""

    def __init__(self, version: str) -> None:
        self.version = version
        self.requests: list[httpx.Request] = []
        self.result: object = []
        self.error: dict | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        body = json.loads(request.content)
        if path == "/web/webclient/version_info":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": {"server_version": self.version}})
        if path == "/jsonrpc":
            params = body["params"]
            if params["service"] == "common":
                return httpx.Response(200, json={"jsonrpc": "2.0", "result": 7})
            if self.error:
                return httpx.Response(200, json={"jsonrpc": "2.0", "error": self.error})
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": self.result})
        if path.startswith("/json/2/"):
            if self.error:
                return httpx.Response(403, json=self.error)
            return httpx.Response(200, json=self.result)
        return httpx.Response(404, text="not found")

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def last_body(self) -> dict:
        return json.loads(self.requests[-1].content)


def make_client(version: str, **profile_kwargs) -> tuple[OdooClient, FakeOdoo]:
    fake = FakeOdoo(version)
    defaults = {"db": "prod", "login": "reader@example.com"}
    profile = InstanceProfile(name="test", url="https://odoo.example.com", api_key=KEY, **{**defaults, **profile_kwargs})
    return OdooClient(profile, transport=httpx.MockTransport(fake)), fake


# --- Lecture seule ---


@pytest.mark.parametrize("method", ["write", "create", "unlink", "copy", "name_create", "action_confirm", "search"])
@pytest.mark.parametrize("version", ["17.0", "19.0"])
def test_forbidden_method_sends_nothing(version: str, method: str) -> None:
    client, fake = make_client(version)
    with pytest.raises(OdooRpcError, match="read-only"):
        client.call("res.partner", method, ids=[1])
    assert fake.requests == []


def test_whitelist_is_exactly_the_read_methods() -> None:
    assert ALLOWED_METHODS == {
        "search_read", "read", "search_count", "fields_get", "read_group", "formatted_read_group"
    }


@pytest.mark.parametrize("model", ["res.partner/../x", "Res.Partner", "res partner", "", "res..partner"])
def test_invalid_model_name_sends_nothing(model: str) -> None:
    client, fake = make_client("19.0")
    with pytest.raises(OdooRpcError, match="invalid model"):
        client.call(model, "read", ids=[1])
    assert fake.requests == []


# --- Transport selon la version ---


@pytest.mark.parametrize("version", ["17.0", "18.0", "saas~17.4+e"])
def test_jsonrpc_for_17_and_18(version: str) -> None:
    client, fake = make_client(version)
    fake.result = [{"id": 1}]
    assert client.call("res.partner", "search_read", domain=[], fields=["name"], context={"bin_size": True}) == [{"id": 1}]
    assert fake.paths() == ["/web/webclient/version_info", "/jsonrpc", "/jsonrpc"]
    args = fake.last_body()["params"]["args"]
    assert args[:5] == ["prod", 7, KEY, "res.partner", "search_read"]
    assert args[5] == []
    assert args[6] == {"domain": [], "fields": ["name"], "context": {"bin_size": True}}
    assert "authorization" not in fake.requests[-1].headers


def test_jsonrpc_authenticates_once_and_passes_ids_positionally() -> None:
    client, fake = make_client("18.0")
    client.call("res.partner", "read", ids=[3, 4], fields=["name"])
    client.call("res.partner", "read", ids=[5], fields=["name"])
    assert fake.paths().count("/jsonrpc") == 3  # 1 authenticate + 2 appels
    assert fake.last_body()["params"]["args"][5] == [[5]]


def test_json2_for_19() -> None:
    client, fake = make_client("19.0")
    fake.result = [{"id": 1}]
    client.call("res.partner", "read", ids=[1], fields=["name"])
    assert fake.paths() == ["/web/webclient/version_info", "/json/2/res.partner/read"]
    request = fake.requests[-1]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["x-odoo-database"] == "prod"
    assert fake.last_body() == {"ids": [1], "fields": ["name"]}
    assert KEY not in request.content.decode()


def test_json2_needs_neither_db_nor_login() -> None:
    client, fake = make_client("19.0", db=None, login=None)
    client.call("res.partner", "search_count", domain=[])
    assert "x-odoo-database" not in fake.requests[-1].headers
    assert "ids" not in fake.last_body()


def test_jsonrpc_requires_db_and_login() -> None:
    client, fake = make_client("17.0", login=None)
    with pytest.raises(OdooRpcError, match="needs 'db' and 'login'"):
        client.call("res.partner", "search_count", domain=[])
    assert fake.paths() == ["/web/webclient/version_info"]


# --- Erreurs et protection de la clé ---


@pytest.mark.parametrize("version", ["17.0", "19.0"])
def test_odoo_error_is_reduced_and_scrubbed(version: str) -> None:
    client, fake = make_client(version)
    payload = {"name": "odoo.exceptions.AccessError", "message": f"denied for {KEY}", "debug": "Traceback ..."}
    fake.error = payload if version == "19.0" else {"code": 200, "message": "Odoo Server Error", "data": payload}
    with pytest.raises(OdooRpcError) as excinfo:
        client.call("res.partner", "read", ids=[1])
    message = str(excinfo.value)
    assert "AccessError" in message
    assert KEY not in message
    assert "Traceback" not in message


def test_redirect_is_not_followed() -> None:
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example.net/"})

    profile = InstanceProfile(name="t", url="https://odoo.example.com", api_key=KEY)
    client = OdooClient(profile, transport=httpx.MockTransport(handler))
    with pytest.raises(OdooRpcError, match="redirect"):
        client.call("res.partner", "read", ids=[1])
    assert len(hits) == 1 and "evil" not in hits[0]


def test_profile_repr_hides_key() -> None:
    assert KEY not in repr(InstanceProfile(name="t", url="https://x", api_key=KEY))


# --- Profils ---


def test_https_required_except_local() -> None:
    with pytest.raises(ProfileError, match="https"):
        parse_profile("p", {"url": "http://odoo.example.com", "api_key": KEY})
    assert parse_profile("p", {"url": "http://localhost:8069/", "api_key": KEY}).url == "http://localhost:8069"
    assert parse_profile("p", {"url": "http://host.docker.internal:8069", "api_key": KEY}).db is None


@pytest.mark.parametrize(
    "data",
    [
        {"url": "https://user:pw@odoo.example.com", "api_key": KEY},
        {"url": "ftp://odoo.example.com", "api_key": KEY},
        {"url": "https://odoo.example.com"},
        {"url": "https://odoo.example.com", "api_key": KEY, "password": "x"},
        "not an object",
    ],
)
def test_invalid_profiles_rejected(data: object) -> None:
    with pytest.raises(ProfileError):
        parse_profile("p", data)


def test_file_is_reloaded_when_it_changes(tmp_path) -> None:
    path = tmp_path / "instances.json"
    path.write_text(json.dumps({"a": {"url": "https://a.example.com", "api_key": "k1"}, "bad": {"url": "http://x.com", "api_key": "k"}}))
    loaded = load_profiles(path)
    assert loaded.profiles["a"].api_key == "k1"
    assert "https" in loaded.errors["bad"]
    with pytest.raises(ProfileError, match="Available: a"):
        get_profile("nope", path)

    path.write_text(json.dumps({"a": {"url": "https://a.example.com", "api_key": "k2-longer"}}))
    os.utime(path, ns=(1, 1))  # force un mtime différent, quel que soit le système de fichiers
    assert get_profile("a", path).api_key == "k2-longer"


def test_invalid_json_does_not_leak_content(tmp_path) -> None:
    path = tmp_path / "instances.json"
    path.write_text('{"a": {"api_key": "' + KEY + '",}}')
    with pytest.raises(ProfileError) as excinfo:
        load_profiles(path)
    assert KEY not in str(excinfo.value)


def test_missing_file(tmp_path) -> None:
    with pytest.raises(ProfileError, match="not found"):
        load_profiles(tmp_path / "absent.json")


# --- Outils ---


@pytest.fixture
def instance(tmp_path, monkeypatch):
    """Un profil 'demo' servi par une FakeOdoo ; renvoie une fabrique (version) -> FakeOdoo."""
    path = tmp_path / "instances.json"
    path.write_text(json.dumps({"demo": {"url": "https://demo.example.com", "db": "d", "login": "l", "api_key": KEY}}))
    monkeypatch.setenv("ODOO_INSTANCES_FILE", str(path))
    monkeypatch.setattr(live_config, "_cache", None)
    live_tools._clients.clear()

    def start(version: str) -> FakeOdoo:
        fake = FakeOdoo(version)
        live_tools._clients["demo"] = OdooClient(get_profile("demo"), transport=httpx.MockTransport(fake))
        return fake

    yield start
    live_tools._clients.clear()


def test_search_records_defaults_and_has_more(instance) -> None:
    fake = instance("19.0")
    fake.result = [{"id": i, "display_name": f"P{i}"} for i in range(3)]
    out = live_tools.search_records("demo", "res.partner", limit=2)
    header, *rows = out.splitlines()
    assert json.loads(header) == {"instance": "demo", "model": "res.partner", "returned": 2, "offset": 0, "has_more": True}
    assert len(rows) == 2
    body = fake.last_body()
    assert body["fields"] == ["display_name"]
    assert body["limit"] == 3
    assert body["context"] == {"bin_size": True, "active_test": True}
    assert KEY not in out


def test_limit_is_capped(instance) -> None:
    fake = instance("19.0")
    live_tools.search_records("demo", "res.partner", limit=100_000)
    assert fake.last_body()["limit"] == live_tools.MAX_LIMIT + 1


def test_star_reads_all_fields_and_string_domain(instance) -> None:
    fake = instance("19.0")
    live_tools.search_records("demo", "res.partner", domain='[["is_company", "=", true]]', fields=["*"])
    assert fake.last_body()["fields"] is None
    assert fake.last_body()["domain"] == [["is_company", "=", True]]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"domain": "not json"},
        {"domain": {"a": 1}},
        {"fields": ["name; drop"]},
        {"order": "name; drop table"},
    ],
)
def test_bad_input_returns_error_without_request(instance, kwargs) -> None:
    fake = instance("19.0")
    assert live_tools.search_records("demo", "res.partner", **kwargs).startswith("Error:")
    assert fake.requests == []


def test_read_records_validates_ids(instance) -> None:
    fake = instance("19.0")
    assert live_tools.read_records("demo", "res.partner", []).startswith("Error:")
    assert live_tools.read_records("demo", "res.partner", ["1"]).startswith("Error:")
    assert live_tools.read_records("demo", "res.partner", list(range(1, 300))).startswith("Error:")
    assert fake.requests == []


def test_unknown_instance(instance) -> None:
    instance("19.0")
    assert "Available: demo" in live_tools.count_records("nope", "res.partner")


def test_group_records_per_version(instance) -> None:
    fake = instance("19.0")
    live_tools.group_records("demo", "sale.order", ["state"], aggregates=["amount_total:sum"])
    assert fake.paths()[-1] == "/json/2/sale.order/formatted_read_group"
    assert fake.last_body()["aggregates"] == ["amount_total:sum", "__count"]

    fake = instance("17.0")
    fake.result = [{"state": "sale", "__count": 4, "__domain": [["state", "=", "sale"]]}]
    out = live_tools.group_records("demo", "sale.order", ["state"], aggregates=["amount_total:sum", "__count"])
    args = fake.last_body()["params"]["args"]
    assert args[4] == "read_group"
    assert args[6]["fields"] == ["amount_total:sum"] and args[6]["lazy"] is False
    assert "__domain" not in out and '"__count": 4' in out


def test_get_fields_filters_and_compacts(instance) -> None:
    fake = instance("18.0")
    fake.result = {
        "partner_id": {"string": "Customer", "type": "many2one", "relation": "res.partner", "required": True, "store": True, "readonly": False},
        "name": {"string": "Reference", "type": "char", "store": True},
    }
    header, *rows = live_tools.get_fields("demo", "sale.order", query="custom").splitlines()
    assert json.loads(header)["fields"] == 1 and json.loads(header)["total"] == 2
    assert json.loads(rows[0]) == {"field": "partner_id", "string": "Customer", "type": "many2one", "relation": "res.partner", "required": True, "store": True}


def test_list_instances_never_shows_key(instance) -> None:
    instance("17.0")
    out = live_tools.list_instances()
    assert '"instance": "demo"' in out and '"version": "17.0"' in out
    assert KEY not in out


def test_list_models_access_error_hint(instance) -> None:
    fake = instance("19.0")
    fake.error = {"name": "odoo.exceptions.AccessError", "message": "no"}
    assert "administration rights" in live_tools.list_models("demo", query="sale")


def test_large_output_is_cut_on_row_boundary(instance) -> None:
    fake = instance("19.0")
    fake.result = [{"id": i, "note": "x" * 1000} for i in range(100)]
    header, *rows = live_tools.search_records("demo", "res.partner", fields=["note"], limit=100).splitlines()
    assert "output_truncated" in json.loads(header)
    assert 0 < len(rows) < 100
    json.loads(rows[-1])  # la dernière ligne reste un JSON complet
