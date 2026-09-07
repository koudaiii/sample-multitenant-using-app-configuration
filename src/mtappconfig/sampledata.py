"""The one set of settings all three samples serve.

Each pattern stores this same data in a different layout; the contract test
asserts that all three resolve it back to identical results.
"""

from __future__ import annotations

from .tenants import Tenant

TENANTS: list[Tenant] = [
    Tenant(tenant_id="tenant-a", display_name="Tenant A"),
    Tenant(tenant_id="tenant-b", display_name="Tenant B"),
]

# Settings that apply to every tenant. Keeping them in one place is the point
# the guidance makes about shared settings: one value, one place to update.
SHARED_SETTINGS: dict[str, str] = {
    "App:SupportEmail": "support@contoso.example",
    "App:Version": "1.4.2",
}

# Per-tenant settings. The guidance names exactly these uses: a tenant's
# database name, and a per-tenant log level for diagnosing one tenant's issue.
TENANT_SETTINGS: dict[str, dict[str, str]] = {
    "tenant-a": {
        "DisplayName": "Tenant A",
        "LogLevel": "Warning",
        "DatabaseName": "db-tenant-a",
        "Features:BetaDashboard": "false",
    },
    "tenant-b": {
        "DisplayName": "Tenant B",
        "LogLevel": "Debug",
        "DatabaseName": "db-tenant-b",
        "Features:BetaDashboard": "true",
        # Overrides the shared value, proving tenant settings win the merge.
        "App:SupportEmail": "vip@contoso.example",
    },
}


def expected_config(tenant_id: str) -> dict[str, str]:
    """The merged result every pattern must produce for this tenant."""
    return {**SHARED_SETTINGS, **TENANT_SETTINGS[tenant_id]}
