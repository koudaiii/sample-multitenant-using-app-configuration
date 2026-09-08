# Sample 04: Snapshot References Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `samples/04-snapshot-references/`, a sample that verifies PR1's claim (from `architecture-center-pr/report.md`) that a tenant-scoped App Configuration key can point at an immutable snapshot, and that repointing the reference rolls a tenant's configuration forward or back with no code change.

**Architecture:** Extend the in-memory `FakeAppConfigurationStore` (`src/mtappconfig/fake.py`) to support named immutable snapshots and a "snapshot reference" setting whose value names a snapshot; `select()` resolves a reference into the snapshot's key-values and merges them in using the same last-write-wins dict-merge the store already uses for ordinary duplicate keys. On top of that store change, sample 04 reuses sample 01's key-prefix selection logic completely unchanged — the only thing that differs between "old config" and "new config" is which snapshot the store's reference key points at.

**Tech Stack:** Python 3.14, Flask, pytest, uv. No Azure SDK, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-02-snapshot-references-sample-design.md`

## Global Constraints

- Do not modify `src/mtappconfig/azure_source.py`. Real snapshot-reference resolution happens inside the Azure SDK's configuration provider; this repo does not verify that provider integration (see the root README's existing "既知の制約" note about `azure_source.py` being unverified).
- Do not modify `TenantConfigSource`, `TenantConfig`, `src/mtappconfig/cache.py`, or `src/mtappconfig/webapp.py`. Snapshot-reference behaviour is confined to `FakeAppConfigurationStore`.
- `FakeSetting.content_type` must default to `None` so every existing `.set()` / `.select()` call and every existing test keeps passing unchanged.
- Do not add sample 04 to `tests/test_pattern_contract.py`'s `SOURCE_FACTORIES`/`SOURCE_IDS`. Sample 04 is not a fourth isolation pattern — tenant-a's resolved values deliberately diverge from `sampledata.expected_config("tenant-a")` once the rollout snapshot is applied, so it would fail that contract test by design.
- Do not add sample 04 to the root README's 01/02/03 comparison table. It gets its own section below the table.
- `main.bicep` for sample 04 grants **App Configuration Data Reader** only (same as sample 01) — no extra role is needed to read a snapshot referenced from a store the identity can already read.

---

### Task 1: Snapshot support in `FakeAppConfigurationStore`

**Files:**
- Modify: `src/mtappconfig/fake.py`
- Test: `tests/test_fake.py`

**Interfaces:**
- Produces: `SNAPSHOT_REFERENCE_CONTENT_TYPE: str` (module constant); `FakeAppConfigurationStore(name: str = "fake", clock: Callable[[], float] = time.monotonic)`; `.create_snapshot(name: str, settings: Mapping[str, str], *, retention_seconds: float | None = None) -> None`; `.set_snapshot_reference(key: str, snapshot_name: str, label: str | None = None) -> None`; `.select(...)` (existing signature, extended behaviour).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_fake.py` (after the existing tests, same file, same `store` fixture is not reused here since these need their own stores):

```python
from mtappconfig.fake import SNAPSHOT_REFERENCE_CONTENT_TYPE


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_snapshot_reference_merges_snapshot_values_into_selection():
    store = FakeAppConfigurationStore()
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Debug"
    }


def test_create_snapshot_copies_values_so_later_mutation_has_no_effect():
    store = FakeAppConfigurationStore()
    source_values = {"LogLevel": "Debug"}
    store.create_snapshot("snap-1", source_values)
    source_values["LogLevel"] = "Trace"
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Debug"
    }


def test_unresolved_snapshot_reference_is_silently_skipped():
    store = FakeAppConfigurationStore()
    store.set("tenant-a/LogLevel", "Warning")
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "missing-snapshot")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Warning"
    }


def test_expired_snapshot_reference_is_silently_skipped():
    clock = FakeClock()
    store = FakeAppConfigurationStore(clock=clock)
    store.create_snapshot("snap-1", {"LogLevel": "Debug"}, retention_seconds=10.0)
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")
    clock.advance(11.0)

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {}


def test_snapshot_reference_wins_when_set_after_the_direct_key():
    store = FakeAppConfigurationStore()
    store.set("tenant-a/LogLevel", "Warning")
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Debug"
    }


def test_a_later_direct_key_overrides_an_earlier_snapshot_reference():
    store = FakeAppConfigurationStore()
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")
    store.set("tenant-a/LogLevel", "Warning")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Warning"
    }


def test_a_reference_to_a_never_created_snapshot_name_matches_no_setting():
    """SNAPSHOT_REFERENCE_CONTENT_TYPE is exercised end-to-end by the tests
    above; this checks the module constant itself is the value select()
    actually compares against, without reaching into the store's internals."""
    store = FakeAppConfigurationStore()
    store.set("tenant-a/ConfigSnapshot", "not-a-reference", label=None)

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "ConfigSnapshot": "not-a-reference"
    }
    assert SNAPSHOT_REFERENCE_CONTENT_TYPE.startswith("application/")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_fake.py -v -k "snapshot"`
Expected: FAIL — `FakeAppConfigurationStore` has no `create_snapshot`/`set_snapshot_reference`, and `mtappconfig.fake` has no `SNAPSHOT_REFERENCE_CONTENT_TYPE`.

- [ ] **Step 3: Implement snapshot support in `src/mtappconfig/fake.py`**

Replace the file's contents with:

```python
"""An in-memory stand-in for one App Configuration store.

It exists so that every pattern, the cache, and the whole test suite run
without an Azure subscription, and so that failure modes (an unreachable
store) can be exercised deliberately.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from .source import ConfigStoreUnavailableError

SNAPSHOT_REFERENCE_CONTENT_TYPE = "application/vnd.microsoft.appconfig.snapshotreference+json"


@dataclass(frozen=True)
class FakeSetting:
    key: str
    value: str
    label: str | None = None
    content_type: str | None = None


@dataclass(frozen=True)
class _Snapshot:
    settings: dict[str, str]
    created_at: float
    retention_seconds: float | None


def _matches_key(key_filter: str, key: str) -> bool:
    """App Configuration supports an exact key or a trailing '*' wildcard."""
    if key_filter == "*":
        return True
    if key_filter.endswith("*"):
        return key.startswith(key_filter[:-1])
    return key == key_filter


def _matches_label(label_filter: str | None, label: str | None) -> bool:
    """A None filter selects only unlabelled settings, matching the service."""
    if label_filter == "*":
        return True
    return label == label_filter


def _trim(key: str, trim_prefixes: Sequence[str]) -> str:
    for prefix in trim_prefixes:
        if key.startswith(prefix):
            return key[len(prefix) :]
    return key


class FakeAppConfigurationStore:
    """One store. Sample 03 creates several of these."""

    def __init__(
        self,
        name: str = "fake",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.request_count = 0
        self.unavailable = False
        self._settings: dict[tuple[str, str | None], FakeSetting] = {}
        self._snapshots: dict[str, _Snapshot] = {}
        self._clock = clock

    def set(self, key: str, value: str, label: str | None = None) -> None:
        self._settings[(key, label)] = FakeSetting(key=key, value=value, label=label)

    def set_many(self, settings: Iterable[FakeSetting]) -> None:
        for setting in settings:
            self.set(setting.key, setting.value, setting.label)

    def create_snapshot(
        self,
        name: str,
        settings: Mapping[str, str],
        *,
        retention_seconds: float | None = None,
    ) -> None:
        """Register an immutable snapshot, copying `settings` at call time.

        Later mutation of the caller's dict, or of the store, must not change
        what this snapshot resolves to.
        """
        self._snapshots[name] = _Snapshot(
            settings=dict(settings),
            created_at=self._clock(),
            retention_seconds=retention_seconds,
        )

    def set_snapshot_reference(
        self, key: str, snapshot_name: str, label: str | None = None
    ) -> None:
        """Register a key whose value names a snapshot to resolve into it.

        The reference key is stored in the same `_settings` dict as ordinary
        key-values, so it is selected, ordered, and overwritten exactly like
        any other setting — only `select()` treats it specially.
        """
        self._settings[(key, label)] = FakeSetting(
            key=key,
            value=snapshot_name,
            label=label,
            content_type=SNAPSHOT_REFERENCE_CONTENT_TYPE,
        )

    def _resolve_snapshot(self, name: str) -> dict[str, str] | None:
        snapshot = self._snapshots.get(name)
        if snapshot is None:
            return None
        if (
            snapshot.retention_seconds is not None
            and self._clock() - snapshot.created_at >= snapshot.retention_seconds
        ):
            return None
        return dict(snapshot.settings)

    def ping(self) -> None:
        if self.unavailable:
            raise ConfigStoreUnavailableError(f"fake store {self.name!r} is unavailable")

    def select(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> dict[str, str]:
        self.ping()
        self.request_count += 1
        selected: dict[str, str] = {}
        for setting in self._settings.values():
            if not _matches_key(key_filter, setting.key):
                continue
            if not _matches_label(label_filter, setting.label):
                continue

            if setting.content_type == SNAPSHOT_REFERENCE_CONTENT_TYPE:
                resolved = self._resolve_snapshot(setting.value)
                if resolved is None:
                    # An unresolved or expired reference contributes nothing
                    # and raises nothing: the provider silently falls back to
                    # whatever other keys are already selected.
                    continue
                for raw_key, value in resolved.items():
                    selected[_trim(raw_key, trim_prefixes)] = value
                continue

            selected[_trim(setting.key, trim_prefixes)] = setting.value
        return selected
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fake.py -v`
Expected: PASS — all existing tests in this file still pass (they don't touch `content_type`), and all new snapshot tests pass.

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `uv run pytest`
Expected: PASS. `FakeSetting`'s new `content_type` field defaults to `None`, so samples 01–03 and the contract test are unaffected.

- [ ] **Step 6: Commit**

```bash
git add src/mtappconfig/fake.py tests/test_fake.py
git commit -m "feat: add snapshot reference support to the fake store"
```

---

### Task 2: Sample 04 source and seed modules

**Files:**
- Create: `samples/04-snapshot-references/source_snapshot_references.py`
- Create: `samples/04-snapshot-references/seed_snapshot_references.py`

**Interfaces:**
- Consumes: `mtappconfig.fake.FakeAppConfigurationStore`, `.create_snapshot`, `.set_snapshot_reference` (Task 1); `mtappconfig.sampledata.SHARED_SETTINGS`, `mtappconfig.sampledata.TENANT_SETTINGS`; `mtappconfig.source.TenantConfig`.
- Produces: `source_snapshot_references.SnapshotReferenceSource` (class, `.name = "snapshot-references"`, `.load(tenant_id: str) -> TenantConfig`, `.ping() -> None`); `source_snapshot_references.SHARED_PREFIX = "_shared/"`; `seed_snapshot_references.build_store() -> FakeAppConfigurationStore`; `seed_snapshot_references.TENANT_A_PREVIOUS_SNAPSHOT`, `.TENANT_A_ROLLOUT_SNAPSHOT`, `.TENANT_B_MISSING_SNAPSHOT` (str constants, used by Task 3's tests and the README's rollback snippet).

- [ ] **Step 1: Create `source_snapshot_references.py`**

```python
"""Pattern 4: sample 01's key-prefix selection, plus a tenant-scoped snapshot
reference that lets a tenant's configuration be rolled out or rolled back by
repointing a key — no application code change, no redeployment.

The selection logic below is intentionally identical to sample 01's
KeyPrefixSource. That is the point of this sample: rollout control is a
store-side behaviour (which snapshot a reference key currently points at),
not an application-side one. The only thing that changed between "the old
configuration" and "the new configuration" is the store's state, seeded by
`seed_snapshot_references.py`.
"""

from __future__ import annotations

from mtappconfig.source import TenantConfig

# See sample 01's source_key_prefix.py for why this prefix, and why a leading
# underscore, is load-bearing.
SHARED_PREFIX = "_shared/"


class SnapshotReferenceSource:
    name = "snapshot-references"

    def __init__(self, store) -> None:
        self._store = store

    def load(self, tenant_id: str) -> TenantConfig:
        def reload() -> dict[str, str]:
            shared = self._store.select(
                key_filter=f"{SHARED_PREFIX}*",
                trim_prefixes=[SHARED_PREFIX],
            )
            # tenant_id has already been validated by TenantRegistry.resolve
            # before it reaches here — see sample 01 for why that matters.
            tenant = self._store.select(
                key_filter=f"{tenant_id}/*",
                trim_prefixes=[f"{tenant_id}/"],
            )
            return {**shared, **tenant}

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload)

    def ping(self) -> None:
        self._store.ping()
```

- [ ] **Step 2: Create `seed_snapshot_references.py`**

```python
"""Lay the canonical sample data out like sample 01, then add two snapshots
and two reference keys so the rollout and fallback paths are both live.

tenant-a's reference key points at the *new* snapshot: the store already
looks like a rollout in progress. tenant-b's reference key points at a
snapshot that doesn't exist, so tenant-b demonstrates the fallback path.
"""

from __future__ import annotations

from mtappconfig.fake import FakeAppConfigurationStore
from mtappconfig.sampledata import SHARED_SETTINGS, TENANT_SETTINGS

from source_snapshot_references import SHARED_PREFIX

TENANT_A_PREVIOUS_SNAPSHOT = "tenant-a-2026-08-01"
TENANT_A_ROLLOUT_SNAPSHOT = "tenant-a-2026-09-01"
TENANT_B_MISSING_SNAPSHOT = "tenant-b-missing"


def build_store() -> FakeAppConfigurationStore:
    store = FakeAppConfigurationStore(name="shared-store")

    for key, value in SHARED_SETTINGS.items():
        store.set(f"{SHARED_PREFIX}{key}", value)
    for tenant_id, settings in TENANT_SETTINGS.items():
        for key, value in settings.items():
            store.set(f"{tenant_id}/{key}", value)

    # The previous snapshot matches tenant-a's direct settings above — it's
    # what a rollback to "no change yet" would restore.
    store.create_snapshot(
        TENANT_A_PREVIOUS_SNAPSHOT,
        {"LogLevel": "Warning", "Features:BetaDashboard": "false"},
    )
    # The rollout snapshot also sets DisplayName, which tenant-a already has
    # set directly above — this is the sample's key-collision demonstration.
    # Because the reference key below is written *after* the direct settings,
    # the snapshot's DisplayName and LogLevel win the merge (see
    # FakeAppConfigurationStore.select in src/mtappconfig/fake.py).
    store.create_snapshot(
        TENANT_A_ROLLOUT_SNAPSHOT,
        {
            "LogLevel": "Debug",
            "Features:BetaDashboard": "true",
            "DisplayName": "Tenant A (rollout)",
        },
    )
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", TENANT_A_ROLLOUT_SNAPSHOT)

    # tenant-b's reference names a snapshot that was never created, so every
    # resolution falls back to tenant-b's directly set values.
    store.set_snapshot_reference("tenant-b/ConfigSnapshot", TENANT_B_MISSING_SNAPSHOT)

    return store
```

- [ ] **Step 3: Sanity-check the wiring by hand**

Run: `uv run python -c "from seed_snapshot_references import build_store; from source_snapshot_references import SnapshotReferenceSource; print(SnapshotReferenceSource(build_store()).load('tenant-a').values)"` from inside `samples/04-snapshot-references/`.
Expected: a dict whose `LogLevel` is `Debug`, `Features:BetaDashboard` is `true`, and `DisplayName` is `Tenant A (rollout)`. (This is a manual check, not a pytest step — Task 3 turns this into real tests.)

- [ ] **Step 4: Commit**

```bash
git add samples/04-snapshot-references/source_snapshot_references.py samples/04-snapshot-references/seed_snapshot_references.py
git commit -m "feat: add sample 04's snapshot-reference source and seed data"
```

---

### Task 3: Sample 04 test suite

**Files:**
- Create: `samples/04-snapshot-references/tests/__init__.py`
- Create: `samples/04-snapshot-references/tests/test_snapshot_references.py`

**Interfaces:**
- Consumes: everything produced by Task 1 and Task 2, plus `mtappconfig.sampledata.{TENANTS, expected_config}`, `mtappconfig.tenants.{TenantRegistry, UnknownTenantError}`, `mtappconfig.webapp.create_app`.

- [ ] **Step 1: Create the empty package marker**

```python
# samples/04-snapshot-references/tests/__init__.py
```

(Empty file — matches the top-level `tests/__init__.py` convention already used by this repo.)

- [ ] **Step 2: Write `test_snapshot_references.py`**

```python
"""Sample 04 verifies PR1's claim (architecture-center-pr/report.md): a
tenant-scoped key that points at a snapshot reference lets a tenant's
configuration be rolled out or rolled back by repointing the reference,
with no application code change.

Each test below is named for the PR1 claim it backs, so the mapping in this
sample's README stays traceable to a specific test.
"""

from __future__ import annotations

import pytest

from mtappconfig.fake import FakeAppConfigurationStore
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

    store.set_snapshot_reference("tenant-a/ConfigSnapshot", TENANT_A_PREVIOUS_SNAPSHOT)
    changed = config.refresh()

    assert changed is True
    assert config.values["LogLevel"] == "Warning"
    assert config.values["Features:BetaDashboard"] == "false"
    # The previous snapshot never mentioned DisplayName, so tenant-a's
    # directly set value shows back through, unaffected by either snapshot.
    assert config.values["DisplayName"] == "Tenant A"


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
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "tenant-a-temp")
    source = SnapshotReferenceSource(store)

    assert source.load("tenant-a").values["LogLevel"] == "Debug"

    clock.advance(61.0)

    assert source.load("tenant-a").values["LogLevel"] == "Warning"


def test_the_snapshot_wins_the_merge_when_the_reference_is_set_last():
    resolved = SnapshotReferenceSource(build_store()).load("tenant-a").values

    # seed_snapshot_references.py sets tenant-a's direct DisplayName before
    # the reference key, so the reference — processed later — wins.
    assert resolved["DisplayName"] == "Tenant A (rollout)"


def test_a_direct_key_set_after_the_reference_wins_the_merge():
    store = FakeAppConfigurationStore()
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")
    store.set("tenant-a/LogLevel", "Warning")

    resolved = SnapshotReferenceSource(store).load("tenant-a").values

    assert resolved["LogLevel"] == "Warning"


def test_tenant_isolation_is_preserved():
    source = SnapshotReferenceSource(build_store())

    tenant_b_values = source.load("tenant-b").values

    assert "Tenant A (rollout)" not in tenant_b_values.values()
    assert tenant_b_values["DatabaseName"] == "db-tenant-b"

    registry = TenantRegistry(TENANTS)
    with pytest.raises(UnknownTenantError):
        registry.resolve("*")


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
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest samples/04-snapshot-references/tests/ -v`
Expected: PASS. Task 1 and Task 2 already implement everything these tests exercise, so there is no red phase for this task — it is the integration check that they compose correctly.

- [ ] **Step 4: Run the full suite to check for regressions**

Run: `uv run pytest`
Expected: PASS, same count of pre-existing tests as before Task 1 plus all new tests from Tasks 1 and 3.

- [ ] **Step 5: Commit**

```bash
git add samples/04-snapshot-references/tests/
git commit -m "test: verify sample 04's rollout, rollback and fallback behaviour"
```

---

### Task 4: Sample 04 app entrypoint and Bicep

**Files:**
- Create: `samples/04-snapshot-references/app.py`
- Create: `samples/04-snapshot-references/main.bicep`

**Interfaces:**
- Consumes: `seed_snapshot_references.build_store`, `source_snapshot_references.SnapshotReferenceSource` (Task 2); `mtappconfig.observability.configure_logging`, `mtappconfig.sampledata.TENANTS`, `mtappconfig.tenants.TenantRegistry`, `mtappconfig.webapp.create_app` (existing core).

- [ ] **Step 1: Create `app.py`**

Identical in structure to `samples/01-shared-store-key-prefix/app.py`, with sample 04's own seed/source modules and pattern name:

```python
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
```

- [ ] **Step 2: Create `main.bicep`**

Identical to `samples/01-shared-store-key-prefix/main.bicep` (single shared store, Data Reader only — reading a referenced snapshot needs no extra role beyond reading the store that names it):

```bicep
targetScope = 'resourceGroup'

@description('Suffix that keeps resource names globally unique.')
param nameSuffix string = uniqueString(resourceGroup().id)

param location string = resourceGroup().location

@allowed([
  'free'
  'developer'
  'standard'
  'premium'
])
param skuName string = 'standard'

@description('Object id of the managed identity that will read configuration.')
param readerPrincipalId string

module monitoring '../../infra/modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    name: 'log-mtappconfig-${nameSuffix}'
    location: location
  }
}

// One shared store, same as samples 01 and 02: the reference key and the
// snapshots it points at both live inside it. Reading a snapshot needs no
// role beyond reading the store, so the RBAC module below is unchanged.
module sharedStore '../../infra/modules/appconfig.bicep' = {
  name: 'shared-store'
  params: {
    name: 'appcs-shared-${nameSuffix}'
    location: location
    skuName: skuName
    logAnalyticsWorkspaceId: monitoring.outputs.id
  }
}

module sharedStoreRbac '../../infra/modules/rbac.bicep' = {
  name: 'shared-store-rbac'
  params: {
    configurationStoreName: sharedStore.outputs.name
    principalId: readerPrincipalId
  }
}

output endpoint string = sharedStore.outputs.endpoint
```

- [ ] **Step 3: Manually verify the app runs**

Run, from the repo root: `cd samples/04-snapshot-references && uv run flask --app app run --port 5004` then in another shell `curl -s localhost:5004/t/tenant-a/api/config` and `curl -s localhost:5004/t/tenant-b/api/config`.
Expected: tenant-a's response has `"LogLevel": "Debug"`; tenant-b's response has `"LogLevel": "Warning"` (its own directly set value, since its reference is unresolved). Stop the server (Ctrl-C) when done.

- [ ] **Step 4: Commit**

```bash
git add samples/04-snapshot-references/app.py samples/04-snapshot-references/main.bicep
git commit -m "feat: add sample 04's Flask entrypoint and Bicep"
```

---

### Task 5: Sample 04 README

**Files:**
- Create: `samples/04-snapshot-references/README.md`

- [ ] **Step 1: Write the README**

```markdown
# 04 — スナップショット参照によるロールアウト制御

サンプル01(共有ストア + キープレフィックス)の**上に築く追加機能**のサンプルです。分離モデルの
4つ目ではありません — テナントの分け方はサンプル01と一切変わらず(`source_snapshot_references.py`
の選択ロジックは01の `KeyPrefixSource` とクラス名・docstring以外同一です)、ストア側の状態
(参照キーがどのスナップショットを指しているか)を変えるだけで配信内容が切り替わることを示します。

対象記事: [Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration) ―
「Configuration rollout and rollback with snapshot references」節
参照: [Snapshot references](https://learn.microsoft.com/azure/azure-app-configuration/concept-snapshot-references)

## 記事の content checklist との対応

記事の提案(PR1)は4項目の content checklist と4項目の definition of done、
計8つのチェック項目を持ちます。前者2項目はコードで検証できるので、このサンプルの
どのテストが裏付けるかを示します。後者2項目(mechanicsをリンクに留める、`ms.date` の扱い)は
記事本文そのものの編集ルールであり、Pythonリポジトリであるこのサンプルの検証対象外です。

| checklist項目 | このサンプルでの検証 |
| --- | --- |
| 参照キーをテナントのキープレフィックス/ラベルでスコープし、テナント・コホート・スタンプが独立したスケジュールで設定を進められ、参照先を変えるだけでロールフォワード/ロールバックできる | `seed_snapshot_references.py`(`tenant-a/ConfigSnapshot` を `tenant_id` プレフィックス配下に配置) / `tests/test_snapshot_references.py::test_rollout_serves_the_new_snapshot_values` と `::test_repointing_the_reference_rolls_back_with_no_code_change` |
| スナップショット参照はテナント分離・認可境界を作らない。アクセスはストアレベルのまま、アプリの ID には参照先スナップショットの読み取り権限が必要 | `main.bicep`(`readerPrincipalId` に付与するのは **App Configuration Data Reader** のみ。追加ロールなし) / `tests/test_snapshot_references.py::test_tenant_isolation_is_preserved` |

`fake.py` 側の解決メカニクス(未解決・期限切れの黙殺、キー衝突順序)は
`tests/test_fake.py` の `test_*_snapshot_reference_*` 系テストが担保します。

## ストアの中身

`seed_snapshot_references.py` がサンプル01と同じ11件のキー・値に加えて、次を追加します。

| 対象 | 内容 |
| --- | --- |
| スナップショット `tenant-a-2026-08-01`(旧) | `LogLevel=Warning`, `Features:BetaDashboard=false` |
| スナップショット `tenant-a-2026-09-01`(新) | `LogLevel=Debug`, `Features:BetaDashboard=true`, `DisplayName=Tenant A (rollout)` |
| `tenant-a/ConfigSnapshot` | 新スナップショットを指す参照キー(＝ロールアウト中の状態でシード) |
| `tenant-b/ConfigSnapshot` | 存在しないスナップショット名 `tenant-b-missing` を指す参照キー(フォールバック実演用) |

新スナップショットの `DisplayName` は tenant-a が直接持つ `DisplayName=Tenant A` と衝突します。
参照キーは直接設定より後に書き込まれるため、`select()` の走査順序どおりスナップショット側が
勝ちます(`src/mtappconfig/fake.py` の `FakeAppConfigurationStore.select`)。

## ロールアウト/ロールバックの操作

新しいHTTPエンドポイントは追加していません(スコープを絞るため)。参照先の切り替えは、
対話的な Python から `set_snapshot_reference` を直接呼び出して行います。

```python
from seed_snapshot_references import build_store, TENANT_A_PREVIOUS_SNAPSHOT
store = build_store()
store.set_snapshot_reference("tenant-a/ConfigSnapshot", TENANT_A_PREVIOUS_SNAPSHOT)  # ロールバック
```

`tests/test_snapshot_references.py::test_repointing_the_reference_rolls_back_with_no_code_change`
が同じ手順(参照先を書き換えて `TenantConfig.refresh()` を呼ぶ)をテストとして担保しています。
README のスニペットとテストが同じ `build_store()` / `set_snapshot_reference()` API を指すので、
片方だけ更新されて食い違うということが起きにくくなっています。

## いつ選ぶか

テナント・テナントコホート・デプロイスタンプが、他と独立したスケジュールで設定をロールアウト/
ロールバックする必要があるとき。分離モデルの選択(01〜03のどれを使うか)とは独立した追加機能です。

## 動かす

```bash
uv run flask --app app run --port 5004
curl -s localhost:5004/t/tenant-a/api/config   # ロールアウト後の値(LogLevel=Debug)
curl -s localhost:5004/t/tenant-b/api/config   # フォールバック値(参照が解決できない)
```

## Azure にデプロイ

```bash
az deployment group create -g <rg> -f main.bicep -p readerPrincipalId=<managed-identity-object-id>
```

`readerPrincipalId` に付与されるのは **App Configuration Data Reader** のみです。スナップショットの
読み取りに追加のロールは要りません(同じ Data Reader で足ります)。実ストアへスナップショットや
参照キーを作成するコードはこのリポジトリのどこにもありません(サンプル01の「実ストアにデータを
入れる」と同じ制約です)。
```

- [ ] **Step 2: Commit**

```bash
git add samples/04-snapshot-references/README.md
git commit -m "docs: add sample 04's README"
```

---

### Task 6: Root README updates

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a new section after the 01/02/03 comparison table**

Find this paragraph in `README.md` (it directly follows the comparison table):

```markdown
**迷ったら 01。** 記事も既定としてキープレフィックスを推奨しています。ラベルをテナント識別に
使うと、バージョニングや環境の区別にラベルを使えなくなるためです。テナントごとに顧客管理キー
(CMK) が必要、またはテナントが設定データの分離を要求する場合にだけ 03 を選びます。
```

Insert a new section immediately after it (before `## 動かす`):

```markdown

## ロールアウト制御(01の上に築く追加機能)

[04 スナップショット参照](samples/04-snapshot-references/) は、01〜03のような分離モデルの
選択とは別軸の追加機能です。テナント・テナントコホート・デプロイスタンプが独立したスケジュールで
設定をロールアウト/ロールバックする必要があるとき、テナントスコープの参照キーを不変スナップショット
へ向け、参照先を変えるだけでコード変更・再デプロイなしに切り替えられることを検証します。
```

- [ ] **Step 2: Add one sentence to "既知の制約"**

Find the end of the existing "既知の制約" section (the last bullet, ending `- \`load(startup_timeout=...)\` の引数名`), and add a new bullet after it:

```markdown
- スナップショット参照の解決は実運用では configuration provider(SDK)側が自動的に行います。
  `samples/04-snapshot-references/` はこの解決ロジックをフェイクストア(`src/mtappconfig/fake.py`)
  内だけで再現しており、`src/mtappconfig/azure_source.py` には変更を加えていません。この未検証性は
  上記の `azure_source.py` 全体の制約に準じます。
```

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest`
Expected: PASS (README changes don't affect tests, but this confirms nothing else broke since Task 1).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: link sample 04 from the root README"
```

---

### Task 7: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite once more from a clean state**

Run: `uv run pytest -v`
Expected: every test passes, including all of samples 01, 02, 03, 04, and the core `tests/` suite. Confirm `tests/test_pattern_contract.py` still only parametrizes `key-prefix`, `label`, `store-per-tenant` (sample 04 must not appear there — see Global Constraints).

- [ ] **Step 2: Confirm `git log` shows one commit per task**

Run: `git log --oneline -8`
Expected: 6 commits from Tasks 1–6 (Task 7 makes no commit of its own — this is a check, not a code change) plus everything already on the branch before this plan started.
```
