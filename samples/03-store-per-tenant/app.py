"""Run with: uv run flask --app app run --port 5003

Set APPCONFIG_SHARED_ENDPOINT and APPCONFIG_ENDPOINTS to point at real stores:

    export APPCONFIG_SHARED_ENDPOINT=https://shared.azconfig.io
    export APPCONFIG_ENDPOINTS='{"tenant-a":"https://a.azconfig.io","tenant-b":"https://b.azconfig.io"}'

Leave them unset to use in-memory fakes.
"""

from __future__ import annotations

import json
import os

import pathlib
import sys

# This project is not installed as a package (see pyproject.toml), so put the
# shared core and this sample's own modules on the path explicitly.
_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2] / "src"))
sys.path.insert(0, str(_HERE.parent))

from mtappconfig.observability import configure_logging
from mtappconfig.sampledata import TENANTS
from mtappconfig.tenants import TenantRegistry
from mtappconfig.webapp import create_app

from seed_store_per_tenant import build_stores
from source_store_per_tenant import StorePerTenantSource


def _build_stores():
    endpoints = os.environ.get("APPCONFIG_ENDPOINTS")
    if not endpoints:
        return build_stores()
    from mtappconfig.azure_source import AzureAppConfigurationStore

    shared_endpoint = os.environ["APPCONFIG_SHARED_ENDPOINT"]
    shared = AzureAppConfigurationStore(shared_endpoint)
    tenant_stores = {
        tenant_id: AzureAppConfigurationStore(endpoint)
        for tenant_id, endpoint in json.loads(endpoints).items()
    }
    return shared, tenant_stores


configure_logging()
registry = TenantRegistry(TENANTS)
shared_store, tenant_stores = _build_stores()
source = StorePerTenantSource(shared_store, tenant_stores)
# Surface a misconfigured deployment now, not on the first request.
source.validate_coverage(registry)

app = create_app(
    source=source,
    registry=registry,
    pattern_name="store-per-tenant",
)
