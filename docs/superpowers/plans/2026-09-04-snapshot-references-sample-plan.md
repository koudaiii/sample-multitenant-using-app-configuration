# Snapshot Reference Engine Implementation Plan

**Goal:** Make Topic 14's `FakeAppConfigurationStore` model Azure App Configuration snapshot
references accurately enough to verify representation, immutable snapshots, safe resolution,
lexicographic conflict handling, expiration fallback, and snapshot-content scope behavior.

**Final scope:** This plan changes only `src/mtappconfig/fake.py`, `tests/test_fake.py`, this plan,
and `docs/superpowers/specs/2026-09-02-snapshot-references-sample-design.md`. Sample 04 application
code, seed data, README, Bicep, root documentation, and the real Azure provider belong to a later
Topic and are not part of PR #18.

**Tech stack:** Python 3.14, pytest, uv. No new dependencies.

## Non-negotiable behavior

- Use the official content type exactly:

  ```python
  SNAPSHOT_REFERENCE_CONTENT_TYPE = (
      'application/json; profile="https://azconfig.io/mime-profiles/snapshot-ref"; charset=utf-8'
  )
  ```

- Store a reference value as a JSON object containing a string `snapshot_name`, never as a bare
  snapshot name.
- Ignore malformed JSON, non-object JSON, missing `snapshot_name`, non-string `snapshot_name`,
  unknown snapshots, and expired snapshots without raising or emitting the reference key.
- Preserve `FakeSetting.content_type` through `set_many()`.
- Copy snapshot input at creation and reject reuse of a snapshot name with `ValueError`; never
  replace the existing snapshot.
- Resolve conflicts by lexicographic source-key order.
- Retain the documented fake-only expiration approximation: retention is measured from creation
  because the fake does not model the real service's archival transition.

## Important security and scope warning

> **A reference key's key filter or label selects the reference only. It does not filter the
> referenced snapshot's contents and does not provide authorization or tenant isolation.**

After an in-scope reference is selected, all keys in that snapshot are merged. A snapshot containing
a foreign tenant's key exposes that key through the selection. Snapshot construction and store
identity/RBAC remain the isolation boundaries. Do not add a second key-filter pass over snapshot
contents, because that would hide the real behavior this fake is intended to expose.

---

### Task 1: Add failing representation and parsing tests

**Files:**
- Test: `tests/test_fake.py`

- [ ] Assert that `SNAPSHOT_REFERENCE_CONTENT_TYPE` is the exact official value.
- [ ] Assert that `set_snapshot_reference("tenant-a/RolloutSnapshot", "snap-1")` stores JSON whose
      decoded object is `{"snapshot_name": "snap-1"}`.
- [ ] Insert a `FakeSetting` reference through `set_many()` and assert that it resolves, proving
      `content_type` is preserved.
- [ ] Parameterize invalid reference values for malformed JSON, arrays, scalar JSON, missing
      `snapshot_name`, and non-string `snapshot_name`; assert each behaves like a missing reference.

Run:

```bash
uv run --offline pytest tests/test_fake.py -q -k "snapshot"
```

Expected before implementation: failures show the invented content type, bare-string value,
`set_many()` content-type loss, and unsafe reference interpretation.

---

### Task 2: Add failing immutable-name test

**Files:**
- Test: `tests/test_fake.py`

- [ ] Keep the existing caller-input copy test.
- [ ] Create `snap-1`, attempt to create `snap-1` again with different contents, and expect:

  ```python
  with pytest.raises(ValueError, match="snapshot 'snap-1' already exists"):
      store.create_snapshot("snap-1", {"LogLevel": "Trace"})
  ```

- [ ] Resolve `snap-1` after the error and assert its original value remains.

This makes both dimensions of immutability explicit: caller mutation cannot change contents and a
name cannot be reused to replace contents.

---

### Task 3: Make ordering tests independent of registration order

**Files:**
- Test: `tests/test_fake.py`

The test data must register settings in the opposite order from the expected lexicographic merge.

Reference wins because `tenant-a/RolloutSnapshot` sorts after `tenant-a/LogLevel`, even though the
reference is registered first:

```python
store = FakeAppConfigurationStore()
store.create_snapshot("snap-1", {"LogLevel": "Debug"})
store.set_snapshot_reference("tenant-a/RolloutSnapshot", "snap-1")
store.set("tenant-a/LogLevel", "Warning")

assert store.select(
    key_filter="tenant-a/*",
    trim_prefixes=["tenant-a/"],
) == {"LogLevel": "Debug"}
```

Direct setting wins because `tenant-a/EarlySnapshot` sorts before `tenant-a/LogLevel`, even though
the reference is registered last:

```python
store = FakeAppConfigurationStore()
store.create_snapshot("snap-1", {"LogLevel": "Debug"})
store.set("tenant-a/LogLevel", "Warning")
store.set_snapshot_reference("tenant-a/EarlySnapshot", "snap-1")

assert store.select(
    key_filter="tenant-a/*",
    trim_prefixes=["tenant-a/"],
) == {"LogLevel": "Warning"}
```

---

### Task 4: Implement the fake-store behavior

**Files:**
- Modify: `src/mtappconfig/fake.py`

- [ ] Import `json`.
- [ ] Replace the invented content type with the official value.
- [ ] Make `set_snapshot_reference()` serialize `{"snapshot_name": snapshot_name}`.
- [ ] Add a parser that catches JSON decoding/type errors, requires a JSON object, and returns only
      a string `snapshot_name`.
- [ ] Treat parser failure exactly like an unknown or expired reference in `select()`.
- [ ] Preserve all `FakeSetting` fields, including `content_type`, in `set_many()`.
- [ ] Before copying a snapshot into `_snapshots`, raise
      `ValueError(f"snapshot {name!r} already exists")` when the name is already present.
- [ ] Keep sorting selected source settings by `setting.key` before merging.
- [ ] Keep resolved snapshot contents unfiltered apart from the existing output-prefix trimming.
- [ ] Keep the creation-time retention approximation and its archival caveat.

Run:

```bash
uv run --offline pytest tests/test_fake.py -q -k "snapshot"
```

Expected after implementation: all snapshot-focused tests pass.

---

### Task 5: Align the design and plan

**Files:**
- Modify: `docs/superpowers/specs/2026-09-02-snapshot-references-sample-design.md`
- Modify: `docs/superpowers/plans/2026-09-04-snapshot-references-sample-plan.md`

- [ ] Describe the official content type and JSON object representation.
- [ ] Describe safe parsing and `set_many()` content-type preservation.
- [ ] Describe immutable names and the explicit `ValueError`.
- [ ] Use the executable `RolloutSnapshot` and `EarlySnapshot` examples above.
- [ ] State lexicographic conflict order and reverse registration order in tests.
- [ ] State the final four-file Topic 14 scope and move sample/application work out of scope.
- [ ] Place the snapshot-content filtering and authorization warning prominently.
- [ ] Retain the archival/expiration approximation disclosure.

---

### Task 6: Verify and publish

Run the focused fake tests:

```bash
uv run --offline pytest tests/test_fake.py -q
```

Run the provider lifecycle regression set:

```bash
uv run --offline pytest \
  tests/test_cache.py \
  tests/test_azure_source.py \
  tests/test_project_setup.py \
  samples/01-shared-store-key-prefix/tests/test_key_prefix.py \
  samples/02-shared-store-label/tests/test_label.py \
  samples/03-store-per-tenant/tests/test_store_per_tenant.py \
  -q
```

Run the full suite and diff checks:

```bash
uv run --offline pytest -q
git diff --check origin/main...HEAD
git diff --stat origin/main...HEAD
```

Confirm the diff remains limited to the final four files, commit with the required trailers, push
`topic/14-snapshot-reference-engine`, update PR #18, and append the task report. Do not merge the PR
or remove the worktree.
