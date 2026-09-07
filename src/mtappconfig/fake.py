"""An in-memory stand-in for one App Configuration store.

It exists so that every pattern, the cache, and the whole test suite run
without an Azure subscription, and so that failure modes (an unreachable
store) can be exercised deliberately.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .source import ConfigStoreUnavailableError


@dataclass(frozen=True)
class FakeSetting:
    key: str
    value: str
    label: str | None = None


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


class FakeAppConfigurationStore:
    """One store. Sample 03 creates several of these."""

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.request_count = 0
        self.unavailable = False
        self._settings: dict[tuple[str, str | None], FakeSetting] = {}

    def set(self, key: str, value: str, label: str | None = None) -> None:
        self._settings[(key, label)] = FakeSetting(key=key, value=value, label=label)

    def set_many(self, settings: Iterable[FakeSetting]) -> None:
        for setting in settings:
            self.set(setting.key, setting.value, setting.label)

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
            key = setting.key
            for prefix in trim_prefixes:
                if key.startswith(prefix):
                    key = key[len(prefix) :]
                    break
            selected[key] = setting.value
        return selected
