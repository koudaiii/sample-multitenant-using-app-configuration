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

    uv pip install -r requirements-azure.txt
    RUN_ID=$(script/bootstrap --azure --sample 04 --subscription <subscription-id> --location <region>)
    export APPCONFIG_ENDPOINT=$(python3 -c 'import json,sys; print(json.load(open(f".runs/{sys.argv[1]}/outputs.json"))["endpoint"]["value"])' "$RUN_ID")
    uv run pytest samples/04-snapshot-references/tests/test_live_snapshot_references.py --run-live -v
    script/cleanup --run "$RUN_ID"

`script/bootstrap --azure --sample 04` (via `script/_seed.bash`) seeds the
real store with exactly the layout this file's tests assert against: the
11 base key-values, the `tenant-a-2026-08-01` (old) and `tenant-a-2026-09-01`
(new, currently referenced) snapshots, `tenant-a/RolloutSnapshot` pointing
at the new snapshot, and `tenant-b/RolloutSnapshot` pointing at a snapshot
name that was never created. This is the same rollout state the fake seeds
by default (see `seed_snapshot_references.py`), independently re-derived
here rather than imported, so a change to the fake's seed data doesn't
silently change what this file claims to have verified against the real
service.

What is verified here, and what is not:

- Reference resolution and tenant isolation (below) are asserted on real
  values read back from a real store.
- Rollback after a real refresh is asserted by writing a real key change
  with the `az` CLI (the same tool `script/_seed.bash` already requires;
  no extra Python SDK dependency) and polling `TenantConfig.refresh()`
  against the real service until it reports a change or a 60s deadline
  passes.
- Read-**denial** without the Data Reader role is explicitly UNVERIFIED
  (see the skipped test below) — this harness does not provision a second,
  deliberately under-permissioned identity. The positive case (a Data
  Reader-scoped identity CAN read) is exercised implicitly by every other
  test in this file succeeding at all.
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
    """AZURE_SUBSCRIPTION_ID if set, else whatever `az` is currently signed
    into — the same fallback script/bootstrap uses when --subscription is
    omitted. Always passed explicitly to az (see script/_common.bash's
    azure() wrapper), so a multi-subscription `az` session fails clearly
    instead of silently targeting the wrong one."""
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


@pytest.mark.live
def test_a_real_store_resolves_the_rollout_snapshot_for_tenant_a():
    """script/_seed.bash leaves tenant-a mid-rollout: its reference points
    at tenant-a-2026-09-01 (LogLevel=Debug, Features:BetaDashboard=true,
    DisplayName=Tenant A (rollout))."""
    store = AzureAppConfigurationStore(_endpoint())
    config = SnapshotReferenceSource(store).load("tenant-a")
    try:
        resolved = config.values

        assert resolved["LogLevel"] == "Debug"
        assert resolved["Features:BetaDashboard"] == "true"
        assert resolved["DisplayName"] == "Tenant A (rollout)"
        # A setting the snapshot doesn't mention is untouched by the reference.
        assert resolved["DatabaseName"] == "db-tenant-a"
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

        assert resolved["DatabaseName"] == "db-tenant-b"
        assert "Tenant A (rollout)" not in resolved.values()
    finally:
        config.close()


@pytest.mark.live
def test_a_real_store_detects_a_rollback_after_a_real_refresh():
    """Repoints tenant-a's reference from the new snapshot back to the old
    one, waits for the real service to actually report the change through
    TenantConfig.refresh(), and confirms the rolled-back values. Restores
    the rollout state afterward so a second run of this file (or of the
    other tests in it) still finds the layout script/_seed.bash created.

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

    _set_rollout_snapshot_reference(store_name, "tenant-a", _PREVIOUS_SNAPSHOT)
    try:
        deadline = time.monotonic() + 60.0
        changed = False
        while time.monotonic() < deadline and not changed:
            time.sleep(2.0)
            changed = config.refresh()

        assert changed, "the real store never reported the rollback within 60s"
        assert config.values["LogLevel"] == "Warning"
        assert config.values["Features:BetaDashboard"] == "false"
        # The old snapshot never mentioned DisplayName, so tenant-a's
        # directly set value shows back through — same claim
        # test_repointing_the_reference_rolls_back_with_no_code_change
        # makes against the fake.
        assert config.values["DisplayName"] == "Tenant A"
    finally:
        try:
            _set_rollout_snapshot_reference(store_name, "tenant-a", _ROLLOUT_SNAPSHOT)
        finally:
            config.close()


@pytest.mark.live
@pytest.mark.skip(
    reason=(
        "UNVERIFIED, not merely skipped by default like the other tests in "
        "this file (which run under --run-live): asserting that an "
        "identity WITHOUT the Data Reader role is denied read access would "
        "require this harness to provision a second, deliberately "
        "under-permissioned identity, which it does not do. The positive "
        "case — a Data Reader-scoped identity can read — is exercised "
        "implicitly by every other test in this file succeeding at all. "
        "See main.bicep and this sample's README for the roles actually "
        "assigned (Data Reader only, plus a temporary Data Owner grant "
        "during seeding that script/bootstrap removes immediately after)."
    )
)
def test_a_real_store_denies_reads_without_the_data_reader_role():
    raise NotImplementedError("see the skip reason above")
