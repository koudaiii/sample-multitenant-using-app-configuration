"""The Flask surface shared by all three samples."""

import pytest

from mtappconfig.cache import TenantConfigCache
from mtappconfig.sampledata import TENANTS
from mtappconfig.source import ConfigStoreUnavailableError, TenantConfig
from mtappconfig.tenants import TenantRegistry
from mtappconfig.webapp import create_app


class StubSource:
    name = "stub"

    def __init__(self):
        self.unavailable = False

    def load(self, tenant_id):
        return TenantConfig(
            tenant_id=tenant_id,
            values={"DisplayName": f"Display {tenant_id}", "LogLevel": "Warning"},
        )

    def ping(self):
        if self.unavailable:
            raise ConfigStoreUnavailableError("stub is down")


@pytest.fixture
def source():
    return StubSource()


@pytest.fixture
def client(source):
    app = create_app(
        source=source,
        registry=TenantRegistry(TENANTS),
        pattern_name="stub-pattern",
    )
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_lists_tenants_and_names_the_pattern(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "tenant-a" in body
    assert "tenant-b" in body
    assert "stub-pattern" in body


def test_tenant_page_renders_resolved_settings(client):
    response = client.get("/t/tenant-a/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Display tenant-a" in body
    assert "LogLevel" in body


def test_tenant_api_returns_json(client):
    response = client.get("/t/tenant-a/api/config")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tenant_id"] == "tenant-a"
    assert payload["pattern"] == "stub-pattern"
    assert payload["values"]["LogLevel"] == "Warning"


def test_unregistered_tenant_is_not_found(client):
    assert client.get("/t/tenant-zzz/").status_code == 404
    assert client.get("/t/tenant-zzz/api/config").status_code == 404


def test_malformed_tenant_id_is_not_found(client):
    """A hostile id must never reach the store."""
    assert client.get("/t/TENANT-A/").status_code == 404
    assert client.get("/t/ab/").status_code == 404


def test_healthz_does_not_touch_the_store(client, source):
    source.unavailable = True

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_readyz_reports_the_store_state(client, source):
    assert client.get("/readyz").status_code == 200

    source.unavailable = True
    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.get_json()["status"] == "unavailable"


def test_cache_diagnostics_expose_hits_and_misses(client):
    client.get("/t/tenant-a/api/config")
    client.get("/t/tenant-a/api/config")

    payload = client.get("/_diagnostics/cache").get_json()

    assert payload["stats"]["misses"] == 1
    assert payload["stats"]["hits"] == 1
    assert payload["entries"] == ["tenant-a"]


def test_an_injected_cache_is_used(source):
    cache = TenantConfigCache(source, max_entries=1)
    app = create_app(
        source=source,
        registry=TenantRegistry(TENANTS),
        pattern_name="stub-pattern",
        cache=cache,
    )
    app.config.update(TESTING=True)
    client = app.test_client()

    client.get("/t/tenant-a/api/config")

    assert cache.stats.misses == 1
