"""Opt-in live verification of sample 04's central claims against a real
Azure App Configuration store — not just the SDK-seam fakes every other
test in this repo uses.

Why this file exists: the only previous live test
(`tests/test_azure_source.py::test_reads_from_a_real_store`) only checks
reachability and that `select()` returns a dict, even against an empty
store. It cannot back the article's central claims about snapshot
references — reference resolution, rollback after a real refresh, and
tenant isolation — because it never asserts on any actual value. This file
does.

Run with:

    # Deploy main.bicep and complete the README's "実ストアにデータを入れる"
    # steps first, keeping the temporary Data Owner assignment until cleanup.
    uv pip install -r requirements-azure.txt
    export APPCONFIG_ENDPOINT=https://<store-name>.azconfig.io
    export AZURE_SUBSCRIPTION_ID=<subscription-id>
    uv run pytest samples/04-snapshot-references/tests/test_live_snapshot_references.py --run-live -v
    az role assignment delete --ids "$OWNER_ASSIGNMENT_ID"

The README's seeding steps create the exact layout this file asserts
against: the 11 base key-values, the `tenant-a-2026-08-01` (old) and
`tenant-a-2026-09-01` (new, currently referenced) snapshots,
`tenant-a/RolloutSnapshot` pointing at the new snapshot, and
`tenant-b/RolloutSnapshot` pointing at a snapshot name that was never
created. This is the same rollout state the fake seeds by default (see
`seed_snapshot_references.py`), independently re-derived here rather than
imported, so a change to the fake's seed data doesn't silently change what
this file claims to have verified against the real service.

What is verified here, and what is not:

- Reference resolution and tenant isolation (below) are asserted on real
  values read back from a real store.
- Rollback after a real refresh is asserted by writing a real key change
  with the `az` CLI (no extra Python SDK dependency) and polling
  `TenantConfig.refresh()` against the real service until it reports a
  change or a 60s deadline passes.
- Read-**denial** without the Data Reader role is explicitly UNVERIFIED
  (see the skipped test below) — this harness does not provision a second,
  deliberately under-permissioned identity.
- Successful reads verify only that the credential selected by
  `DefaultAzureCredential` can read the store. The documented local path
  uses the active developer credential and deliberately retains its
  temporary Data Owner assignment so the rollback test can mutate the
  reference. This harness neither identifies the selected credential nor
  inspects its effective RBAC roles, so Data Reader-only access is also
  UNVERIFIED.
- To verify Data Reader-only access separately, seed with an operator
  identity, run only the two read-only tests from a hosted or alternate
  identity whose effective access has independently been limited to App
  Configuration Data Reader, and configure `DefaultAzureCredential` to use
  that identity. Do not run the rollback test with that identity because
  it requires write access.
- The SDK version actually exercised is whatever `uv pip install -r
  requirements-azure.txt` resolves at run time — record it here after a
  real run: `azure-appconfiguration-provider==<record the version here>`.
  Nothing in this repository has run these tests against a real store as
  of the commit that added this file.
"""

from __future__ import annotations

import os
import subprocess
import time
from urllib.parse import urlparse

import pytest

from mtappconfig.azure_source import AzureAppConfigurationStore

from source_snapshot_references import SnapshotReferenceSource

_SNAPSHOT_REFERENCE_CONTENT_TYPE = (
    'application/json; profile="https://azconfig.io/mime-profiles/snapshot-ref"; charset=utf-8'
)
_ROLLOUT_SNAPSHOT = "tenant-a-2026-09-01"
_PREVIOUS_SNAPSHOT = "tenant-a-2026-08-01"
_TENANT_B_MISSING_SNAPSHOT = "tenant-b-missing"


def _endpoint() -> str:
    try:
        return os.environ["APPCONFIG_ENDPOINT"]
    except KeyError:
        pytest.skip("APPCONFIG_ENDPOINT is not set; see this file's module docstring.")


def _store_name(endpoint: str) -> str:
    hostname = urlparse(endpoint).hostname
    assert hostname is not None, f"could not parse a hostname from {endpoint!r}"
    return hostname.split(".")[0]


def _subscription() -> str:
    """Use AZURE_SUBSCRIPTION_ID or the subscription selected by `az`."""
    subscription = os.environ.get("AZURE_SUBSCRIPTION_ID")
    if subscription:
        return subscription
    result = subprocess.run(
        ["az", "account", "show", "--query", "id", "-o", "tsv", "--only-show-errors"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _set_rollout_snapshot_reference(store_name: str, tenant_id: str, snapshot_name: str) -> None:
    """Write the same official JSON value/content-type represented by the fake."""
    subprocess.run(
        [
            "az",
            "appconfig",
            "kv",
            "set",
            "--name",
            store_name,
            "--subscription",
            _subscription(),
            "--only-show-errors",
            "--auth-mode",
            "login",
            "--yes",
            "--key",
            f"{tenant_id}/RolloutSnapshot",
            "--content-type",
            _SNAPSHOT_REFERENCE_CONTENT_TYPE,
            "--value",
            f'{{"snapshot_name": "{snapshot_name}"}}',
            "-o",
            "none",
        ],
        check=True,
    )


def _wait_for_log_level(config, expected: str) -> None:
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        time.sleep(2.0)
        if config.refresh() and config.values["LogLevel"] == expected:
            return
    pytest.fail(f"the real store never refreshed LogLevel to {expected!r} within 60s")


@pytest.mark.live
def test_a_real_store_resolves_the_rollout_snapshot_for_tenant_a():
    """The README seeding steps leave tenant-a mid-rollout, pointing at
    tenant-a-2026-09-01 (LogLevel=Debug, Features:BetaDashboard=true,
    DisplayName=Tenant A (rollout))."""
    store = AzureAppConfigurationStore(_endpoint())
    config = SnapshotReferenceSource(store).load("tenant-a")
    try:
        resolved = config.values

        assert resolved == {
            "App:SupportEmail": "support@contoso.example",
            "App:Version": "1.4.2",
            "DatabaseName": "db-tenant-a",
            "DisplayName": "Tenant A (rollout)",
            "Features:BetaDashboard": "true",
            "LogLevel": "Debug",
        }
    finally:
        config.close()


@pytest.mark.live
def test_a_real_store_isolates_tenant_b_from_tenant_as_snapshot():
    """tenant-b/RolloutSnapshot names a snapshot that was never created, so
    tenant-b must fall back to its own direct values without error, and
    must never see any value from tenant-a's rollout snapshot."""
    store = AzureAppConfigurationStore(_endpoint())
    config = SnapshotReferenceSource(store).load("tenant-b")
    try:
        resolved = config.values

        assert resolved == {
            "App:SupportEmail": "vip@contoso.example",
            "App:Version": "1.4.2",
            "DatabaseName": "db-tenant-b",
            "DisplayName": "Tenant B",
            "Features:BetaDashboard": "true",
            "LogLevel": "Debug",
        }, f"expected direct-value fallback from missing snapshot {_TENANT_B_MISSING_SNAPSHOT!r}"
    finally:
        config.close()


@pytest.mark.live
def test_a_real_store_detects_a_rollback_after_a_real_refresh():
    """Repoints tenant-a's reference from the new snapshot back to the old
    one, waits for the real service to actually report the change through
    TenantConfig.refresh(), and confirms the rolled-back values. Restores
    the rollout state afterward so a second run of this file (or of the
    other tests in it) still finds the layout the README steps created.

    A short refresh_interval_seconds keeps this test from needing to wait
    out this repo's default 30s interval — the real service's own
    propagation time, not this local setting, is what the polling loop
    below actually waits on.
    """
    endpoint = _endpoint()
    store_name = _store_name(endpoint)
    store = AzureAppConfigurationStore(endpoint, refresh_interval_seconds=1.0)
    config = SnapshotReferenceSource(store).load("tenant-a")
    assert config.values["LogLevel"] == "Debug"

    try:
        _set_rollout_snapshot_reference(store_name, "tenant-a", _PREVIOUS_SNAPSHOT)
        try:
            _wait_for_log_level(config, "Warning")
            assert config.values["LogLevel"] == "Warning"
            assert config.values["Features:BetaDashboard"] == "false"
            # The old snapshot and tenant-a's direct setting agree on this value.
            assert config.values["DisplayName"] == "Tenant A"
        finally:
            _set_rollout_snapshot_reference(store_name, "tenant-a", _ROLLOUT_SNAPSHOT)

        _wait_for_log_level(config, "Debug")
        assert config.values["Features:BetaDashboard"] == "true"
        assert config.values["DisplayName"] == "Tenant A (rollout)"
    finally:
        config.close()


@pytest.mark.live
@pytest.mark.skip(
    reason=(
        "UNVERIFIED, not merely skipped by default like the other tests in "
        "this file (which run under --run-live): asserting that an "
        "identity WITHOUT the Data Reader role is denied read access would "
        "require this harness to provision a second, deliberately "
        "under-permissioned identity, which it does not do. Successful "
        "tests only prove that the credential selected by "
        "DefaultAzureCredential can read; the documented local path may "
        "retain Data Owner for mutation, and this harness does not inspect "
        "the selected credential or its effective roles. A separate "
        "Data Reader-only hosted or alternate-identity run is also "
        "unverified; see this file's module docstring and the sample README."
    )
)
def test_a_real_store_denies_reads_without_the_data_reader_role():
    raise NotImplementedError("see the skip reason above")
