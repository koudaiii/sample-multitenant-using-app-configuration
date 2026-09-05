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

    def close(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> None:
        """No-op because the fake store holds no provider resources."""

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
