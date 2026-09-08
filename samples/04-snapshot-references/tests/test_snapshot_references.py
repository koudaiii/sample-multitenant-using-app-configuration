"""Sample 04 verifies PR1's claim
(docs/superpowers/specs/2026-09-02-snapshot-references-sample-design.md): a
tenant-scoped key that points at a snapshot reference lets a tenant's
configuration be rolled out or rolled back by repointing the reference,
with no application code change.

Each test below is named for the PR1 claim it backs, so the mapping in this
sample's README stays traceable to a specific test.
"""

from __future__ import annotations

import runpy
import re
import shlex
import subprocess
import os
from pathlib import Path

import pytest

from mtappconfig.fake import FakeAppConfigurationStore, FakeSetting
from mtappconfig.sampledata import TENANTS, expected_config
from mtappconfig.tenants import TenantRegistry, UnknownTenantError
from mtappconfig.webapp import create_app

from seed_snapshot_references import (
    TENANT_A_PREVIOUS_SNAPSHOT,
    TENANT_A_ROLLOUT_SNAPSHOT,
    build_store,
)
from source_snapshot_references import SnapshotReferenceSource


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _GuardedStore:
    def __init__(self):
        self.select_calls = []
        self.close_calls = []

    def select(self, key_filter="*", label_filter=None, trim_prefixes=()):
        self.select_calls.append(
            {
                "key_filter": key_filter,
                "label_filter": label_filter,
                "trim_prefixes": tuple(trim_prefixes),
            }
        )
        return {}

    def close(self, key_filter="*", label_filter=None, trim_prefixes=()):
        self.close_calls.append(
            {
                "key_filter": key_filter,
                "label_filter": label_filter,
                "trim_prefixes": tuple(trim_prefixes),
            }
        )

    def ping(self):
        pass


def test_rollout_serves_the_new_snapshot_values():
    """The store is seeded already mid-rollout: tenant-a's reference points
    at the new snapshot."""
    resolved = SnapshotReferenceSource(build_store()).load("tenant-a").values

    assert resolved["LogLevel"] == "Debug"
    assert resolved["Features:BetaDashboard"] == "true"
    assert resolved["DisplayName"] == "Tenant A (rollout)"
    # Settings the snapshot doesn't mention are untouched.
    assert resolved["DatabaseName"] == "db-tenant-a"
    assert resolved["App:SupportEmail"] == "support@contoso.example"


def test_repointing_the_reference_rolls_back_with_no_code_change():
    store = build_store()
    config = SnapshotReferenceSource(store).load("tenant-a")
    assert config.values["LogLevel"] == "Debug"

    store.set_snapshot_reference("tenant-a/RolloutSnapshot", TENANT_A_PREVIOUS_SNAPSHOT)
    changed = config.refresh()

    assert changed is True
    assert config.values["LogLevel"] == "Warning"
    assert config.values["Features:BetaDashboard"] == "false"
    # The previous snapshot and direct settings agree on the baseline.
    assert config.values["DisplayName"] == "Tenant A"


def test_standalone_rollback_readme_command_runs_from_the_repository_root():
    sample_dir = Path(__file__).resolve().parents[1]
    blocks = re.findall(r"```bash\n(.*?)```", (sample_dir / "README.md").read_text(), re.S)
    commands = [block for block in blocks if "from seed_snapshot_references import" in block]
    assert len(commands) == 1, "provide an executable shell command, not an unconfigured Python snippet"
    command = commands[0]
    assert "PYTHONPATH=src:samples/04-snapshot-references" in command
    lockfile = sample_dir.parents[1] / "uv.lock"
    original_lock = lockfile.read_bytes()

    result = subprocess.run(
        ["bash", "-c", command],
        cwd=sample_dir.parents[1],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "UV_FROZEN": "true"},
    )

    assert result.stdout.splitlines() == ["ロールアウト中: Debug", "ロールバック後: Warning"]
    assert lockfile.read_bytes() == original_lock


def test_fake_seed_matches_the_documented_live_seed_baseline_and_snapshots():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    seed_steps = readme.split("### 1. ", 1)[1].split('export APPCONFIG_ENDPOINT=', 1)[0]
    live_layout = FakeAppConfigurationStore()
    for block in re.findall(r"```bash\n(.*?)```", seed_steps, re.S):
        for line in block.replace("\\\n", "").splitlines():
            args = shlex.split(line, comments=True)
            if args[:4] == ["az", "appconfig", "kv", "set"]:
                key = args[args.index("--key") + 1]
                value = args[args.index("--value") + 1]
                content_type = (
                    args[args.index("--content-type") + 1] if "--content-type" in args else None
                )
                live_layout.set_many([FakeSetting(key=key, value=value, content_type=content_type)])
            elif args[:4] == ["az", "appconfig", "snapshot", "create"]:
                assert args[args.index("--filters") + 1] == '{"key":"tenant-a/*"}'
                live_layout.create_snapshot(
                    args[args.index("--snapshot-name") + 1],
                    live_layout.select(key_filter="tenant-a/*"),
                )

    fake_layout = build_store()
    assert fake_layout._settings == live_layout._settings
    for name in (TENANT_A_PREVIOUS_SNAPSHOT, TENANT_A_ROLLOUT_SNAPSHOT):
        assert fake_layout._resolve_snapshot(name) == live_layout._resolve_snapshot(name)


def test_an_unresolved_reference_falls_back_to_direct_values_without_error():
    """tenant-b's reference names a snapshot that was never created."""
    resolved = SnapshotReferenceSource(build_store()).load("tenant-b").values

    assert resolved == expected_config("tenant-b")


def test_an_expired_snapshot_falls_back_to_direct_values_without_error():
    clock = FakeClock()
    store = FakeAppConfigurationStore(clock=clock)
    store.set("tenant-a/DisplayName", "Tenant A")
    store.set("tenant-a/LogLevel", "Warning")
    store.create_snapshot("tenant-a-temp", {"LogLevel": "Debug"}, retention_seconds=60.0)
    # "RolloutSnapshot" (R) sorts lexicographically after "LogLevel" (L), so
    # the reference wins the merge while it resolves, regardless of write
    # order.
    store.set_snapshot_reference("tenant-a/RolloutSnapshot", "tenant-a-temp")
    source = SnapshotReferenceSource(store)

    assert source.load("tenant-a").values["LogLevel"] == "Debug"

    clock.advance(61.0)

    assert source.load("tenant-a").values["LogLevel"] == "Warning"


def test_the_snapshot_wins_the_merge_because_its_reference_key_sorts_last():
    resolved = SnapshotReferenceSource(build_store()).load("tenant-a").values

    # seed_snapshot_references.py names tenant-a's reference key
    # "RolloutSnapshot", which sorts lexicographically after "DisplayName"
    # (and after "DatabaseName", "Features:BetaDashboard", and "LogLevel").
    # The store resolves same-name-key conflicts by lexicographic order of
    # the key name, not by write order, so the snapshot's DisplayName wins.
    assert resolved["DisplayName"] == "Tenant A (rollout)"


def test_a_direct_key_wins_when_its_name_sorts_after_the_reference_key():
    store = FakeAppConfigurationStore()
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    # "ConfigSnapshot" (C) sorts lexicographically before "LogLevel" (L), so
    # the direct key below wins the merge regardless of write order.
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")
    store.set("tenant-a/LogLevel", "Warning")

    resolved = SnapshotReferenceSource(store).load("tenant-a").values

    assert resolved["LogLevel"] == "Warning"


def test_a_snapshot_containing_another_tenants_keys_leaks_them_unfiltered():
    """Scoping the reference key under tenant-a/ decides which reference is
    picked up — it does not filter what the referenced snapshot contains.
    If a snapshot is built with another tenant's keys by mistake, those keys
    merge straight into the resolving tenant's config. This mirrors the real
    service's documented behavior; building a correctly-scoped snapshot is
    the operator's responsibility (see README's "重要な注意" section)."""
    store = build_store()
    store.create_snapshot(
        "tenant-a-2026-09-20-built-wrong",
        {"LogLevel": "Debug", "tenant-b/DatabaseName": "db-tenant-b"},
    )
    store.set_snapshot_reference("tenant-a/RolloutSnapshot", "tenant-a-2026-09-20-built-wrong")

    resolved = SnapshotReferenceSource(store).load("tenant-a").values

    assert resolved["tenant-b/DatabaseName"] == "db-tenant-b"


def test_tenant_isolation_is_preserved():
    source = SnapshotReferenceSource(build_store())

    tenant_b_values = source.load("tenant-b").values

    assert "Tenant A (rollout)" not in tenant_b_values.values()
    assert tenant_b_values["DatabaseName"] == "db-tenant-b"

    registry = TenantRegistry(TENANTS)
    with pytest.raises(UnknownTenantError):
        registry.resolve("*")


def test_tenant_config_close_drops_only_its_tenant_prefix_provider():
    store = _GuardedStore()
    config = SnapshotReferenceSource(store).load("tenant-a")

    assert config.close is not None
    config.close()

    assert store.close_calls == [
        {
            "key_filter": "tenant-a/*",
            "label_filter": None,
            "trim_prefixes": ("tenant-a/",),
        }
    ]


def test_app_module_uses_the_fake_store_when_endpoint_is_unset(monkeypatch):
    from mtappconfig import azure_source

    monkeypatch.delenv("APPCONFIG_ENDPOINT", raising=False)
    monkeypatch.setattr(
        azure_source,
        "AzureAppConfigurationStore",
        lambda endpoint: pytest.fail(f"unexpected Azure store for {endpoint}"),
    )

    module_globals = runpy.run_path(Path(__file__).resolve().parents[1] / "app.py")
    payload = module_globals["app"].test_client().get("/t/tenant-a/api/config").get_json()

    assert payload["values"]["LogLevel"] == "Debug"
    assert payload["pattern"] == "snapshot-references"


def test_app_module_rejects_an_empty_present_endpoint(monkeypatch):
    monkeypatch.setenv("APPCONFIG_ENDPOINT", "")

    with pytest.raises(
        ValueError,
        match="APPCONFIG_ENDPOINT must be a non-empty HTTPS URL",
    ):
        runpy.run_path(Path(__file__).resolve().parents[1] / "app.py")


def test_app_module_uses_the_azure_store_when_endpoint_is_set(monkeypatch):
    from mtappconfig import azure_source

    constructed_endpoints = []

    def build_azure_store(endpoint):
        constructed_endpoints.append(endpoint)
        return build_store()

    monkeypatch.setenv("APPCONFIG_ENDPOINT", "https://example.azconfig.io")
    monkeypatch.setattr(
        azure_source,
        "AzureAppConfigurationStore",
        build_azure_store,
    )

    module_globals = runpy.run_path(Path(__file__).resolve().parents[1] / "app.py")
    payload = module_globals["app"].test_client().get("/t/tenant-a/api/config").get_json()

    assert constructed_endpoints == ["https://example.azconfig.io"]
    assert payload["values"]["LogLevel"] == "Debug"
    assert payload["pattern"] == "snapshot-references"


@pytest.mark.parametrize(
    "path",
    [
        "/t/*/api/config",
        "/t/tenant-a%0A/api/config",
        "/t/tenant-zzz/api/config",
        "/t/tenant-a%2Fapi/config",
    ],
)
def test_http_rejects_tenant_boundary_attacks_before_building_key_filters(path):
    store = _GuardedStore()
    app = create_app(
        source=SnapshotReferenceSource(store),
        registry=TenantRegistry(TENANTS),
        pattern_name="snapshot-references",
    )
    app.config.update(TESTING=True)

    response = app.test_client().get(path)

    assert response.status_code == 404
    assert store.select_calls == []


def test_http_config_endpoint_serves_rollout_and_fallback_without_500():
    app = create_app(
        source=SnapshotReferenceSource(build_store()),
        registry=TenantRegistry(TENANTS),
        pattern_name="snapshot-references",
    )
    app.config.update(TESTING=True)
    client = app.test_client()

    resp_a = client.get("/t/tenant-a/api/config")
    assert resp_a.status_code == 200
    assert resp_a.get_json()["values"]["LogLevel"] == "Debug"

    resp_b = client.get("/t/tenant-b/api/config")
    assert resp_b.status_code == 200
    assert resp_b.get_json()["values"] == expected_config("tenant-b")
