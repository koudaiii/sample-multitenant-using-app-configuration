"""Run with: uv run flask --app app run --port 5004

Set APPCONFIG_ENDPOINT to point at a real store; leave it unset to use the
in-memory fake (already seeded mid-rollout for tenant-a; see
seed_snapshot_references.py).
"""

from __future__ import annotations

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

from seed_snapshot_references import build_store
from source_snapshot_references import SnapshotReferenceSource


def _build_store():
    endpoint = os.environ.get("APPCONFIG_ENDPOINT")
    if not endpoint:
        return build_store()
    from mtappconfig.azure_source import AzureAppConfigurationStore

    return AzureAppConfigurationStore(endpoint)


configure_logging()
app = create_app(
    source=SnapshotReferenceSource(_build_store()),
    registry=TenantRegistry(TENANTS),
    pattern_name="snapshot-references",
)
