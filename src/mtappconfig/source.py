"""The abstraction that every tenancy pattern implements.

Keeping this to one protocol is what lets the three samples share a Flask
application, a cache, and a test suite, so that the only thing that differs
between them is how a tenant's settings are selected from a store.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class ConfigStoreUnavailableError(RuntimeError):
    """The backing configuration store could not be reached."""


class ConfigValues(dict[str, str]):
    """Values plus refresh failures observed during this particular read.

    A cached provider can supply usable values even when refreshing it fails.
    The failure channel is metadata, never a configuration key or a claim
    that an error-free read contacted the service.
    """

    def __init__(
        self, values: Mapping[str, str], *, refresh_errors: Iterable[Exception] = ()
    ) -> None:
        super().__init__(values)
        self.refresh_errors: tuple[Exception, ...] = tuple(refresh_errors)


def merge_config_values(*selections: Mapping[str, str]) -> ConfigValues:
    values: dict[str, str] = {}
    errors: list[Exception] = []
    for selection in selections:
        values.update(selection)
        if isinstance(selection, ConfigValues):
            errors.extend(selection.refresh_errors)
    return ConfigValues(values, refresh_errors=errors)


@dataclass
class TenantConfig:
    """Resolved settings and failures from the latest load/refresh attempt.

    Passive cache hits do not represent a new attempt or replay its failures.
    """

    tenant_id: str
    values: Mapping[str, str]
    reload: Callable[[], Mapping[str, str]] | None = field(default=None, repr=False)
    close: Callable[[], None] | None = field(default=None, repr=False)
    refresh_errors: tuple[Exception, ...] = field(default=(), init=False, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.values, ConfigValues):
            self.refresh_errors = self.values.refresh_errors
            self.values = dict(self.values)

    def refresh(self) -> bool:
        """Reload from the store. Returns True when a value actually changed.

        Reported refresh errors preserve the complete last-good tenant view;
        callers can inspect refresh_errors even when no value changed.
        Callers are expected to rate-limit this; see `TenantConfigCache`.
        """
        self.refresh_errors = ()
        if self.reload is None:
            return False
        latest = self.reload()
        if isinstance(latest, ConfigValues):
            self.refresh_errors = latest.refresh_errors
            if self.refresh_errors:
                return False
            latest = dict(latest)
        if latest == self.values:
            return False
        self.values = latest
        return True


@runtime_checkable
class TenantConfigSource(Protocol):
    """Selects one tenant's settings out of one or more stores."""

    name: str

    def load(self, tenant_id: str) -> TenantConfig:
        """Load settings for an already-validated tenant identifier."""
        ...

    def ping(self) -> None:
        """Raise ConfigStoreUnavailableError if the store is unreachable."""
        ...
