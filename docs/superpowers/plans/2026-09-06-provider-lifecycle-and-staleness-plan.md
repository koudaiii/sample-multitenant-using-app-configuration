# Provider Lifecycle and Staleness Implementation Plan

**Status:** Implemented in Topic 11 / PR #15.

**Goal:** Make the real Azure provider safe at the point it is introduced by
linking tenant-cache eviction and expiry to disposal of the corresponding SDK
provider.

**Final approach:** `TenantConfig` carries an optional `close` callback.
`TenantConfigCache` invokes it before removing an expired or evicted entry.
Each sample closes only the provider owned by that tenant. The Azure adapter
removes and closes the provider cached for that exact query. A later load of
the same tenant therefore creates a fresh provider.

## Scope

- `src/mtappconfig/source.py`
- `src/mtappconfig/cache.py`
- `src/mtappconfig/fake.py`
- `src/mtappconfig/azure_source.py`
- `samples/01-shared-store-key-prefix/source_key_prefix.py`
- `samples/02-shared-store-label/source_label.py`
- `samples/03-store-per-tenant/source_store_per_tenant.py`
- `tests/test_cache.py`
- `tests/test_azure_source.py`
- Focused tests in samples 01-03

## Final contracts

### `TenantConfig`

`TenantConfig.close` is an optional, non-repr callback:

```python
close: Callable[[], None] | None = field(default=None, repr=False)
```

Existing sources that do not own disposable resources can omit it.

### `TenantConfigCache`

The cache calls `close`:

1. before deleting a TTL-expired entry;
2. after removing the least-recently-used entry from the ordered map.

Close failures are logged and swallowed. They must not prevent expiry,
eviction, or loading another tenant.

The cache continues to hold its single lock for the complete `get` operation.
That includes closing an expired entry and loading its replacement.

### Store adapters

Both store adapters expose:

```python
close(
    key_filter: str = "*",
    label_filter: str | None = None,
    trim_prefixes: Sequence[str] = (),
) -> None
```

The fake implementation is a no-op.

The Azure implementation builds the same query tuple used by `select`, removes
that entry from `_providers`, and closes only the removed provider. Closing an
unknown query is a no-op. Because the provider is removed first, the next
identical `select` must call the SDK loader and create a fresh provider.

### Sample ownership

| Sample | Tenant-owned provider closed | Shared provider |
| --- | --- | --- |
| 01 key prefix | `key_filter=f"{tenant_id}/*"` with the tenant trim prefix | retained |
| 02 label | `key_filter="*"`, `label_filter=tenant_id` | retained |
| 03 store per tenant | the tenant store's `key_filter="*"` provider | retained |

No tenant eviction may close a provider used by other tenants.

## Implementation sequence

### 1. Add cache lifecycle tests

Extend the recording source in `tests/test_cache.py` with a close callback and
verify:

- LRU eviction closes the evicted tenant only;
- TTL expiry closes the expired tenant;
- close failure does not prevent eviction;
- a missing close callback remains valid.

Run these tests before and after implementing the lifecycle field and cache
hooks.

### 2. Add the lifecycle field and cache hooks

- Add `TenantConfig.close`.
- Add `FakeAppConfigurationStore.close`.
- Call a shared `_safe_close` helper from expiry and eviction paths.
- Keep close failures observable through the existing logger.

### 3. Add Azure provider disposal tests

Using the `_import_sdk` seam in `tests/test_azure_source.py`, verify:

- closing one exact query closes only its provider;
- another query, including a shared query, remains open;
- selecting the closed query again creates a new provider;
- closing a query that has not been loaded is harmless.

### 4. Implement exact Azure provider disposal

Use `(key_filter, label_filter, tuple(trim_prefixes))` as the close key, exactly
matching `select`. Pop the provider before calling its `close` method.

### 5. Wire samples 01-03

Each source returns a `TenantConfig` whose close callback uses the same
tenant-specific query used by its reload function. Add focused sample tests
that assert the exact close arguments and prove the shared provider is not
closed.

### 6. Guard the provider load contract

Strengthen the Azure test seam so a main provider load asserts:

- endpoint;
- credential object presence;
- selector key and label;
- trim prefixes;
- `refresh_enabled=True`;
- refresh interval;
- startup timeout.

These assertions cover the adapter-to-SDK contract without importing the
optional Azure packages or relying on SDK implementation details.

### 7. Verify

Run:

```bash
uv run --offline pytest tests/test_cache.py tests/test_azure_source.py tests/test_project_setup.py -q
uv run --offline pytest -q
git diff --check
```

## Accepted trade-offs

- Tenant-provider staleness is bounded indirectly by the outer cache TTL:
  expiry closes and removes the tenant provider, and the replacement load uses
  a fresh provider.
- Shared providers are deliberately retained because they are used by every
  tenant. Their staleness is therefore not bounded by per-tenant TTL.
- A fresh replacement load runs while `TenantConfigCache` holds its global
  lock. A slow or unavailable store can block unrelated tenants until that
  load returns or reaches its startup timeout.

## Completion criteria

- Expiry and eviction invoke the tenant's close callback.
- Azure close removes and closes only the exact provider.
- A later identical select creates a fresh provider.
- Samples 01-03 never close shared providers during one tenant's eviction.
- Close failures do not break cache progress.
- Provider-load arguments are covered through the SDK seam.
- Focused and full Python tests pass, and `git diff --check` is clean.
