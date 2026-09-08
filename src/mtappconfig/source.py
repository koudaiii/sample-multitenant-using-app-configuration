"""The abstraction that every tenancy pattern implements.

Keeping this to one protocol is what lets the three samples share a Flask
application, a cache, and a test suite, so that the only thing that differs
between them is how a tenant's settings are selected from a store.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class ConfigStoreUnavailableError(RuntimeError):
    """The backing configuration store could not be reached."""


@dataclass
class TenantConfig:
    """One tenant's resolved settings, plus a way to reload them."""

    tenant_id: str
    values: Mapping[str, str]
    reload: Callable[[], Mapping[str, str]] | None = field(default=None, repr=False)
    close: Callable[[], None] | None = field(default=None, repr=False)

    def refresh(self) -> bool:
        """Reload from the store. Returns True when a value actually changed.

        Callers are expected to rate-limit this; see `TenantConfigCache`.
        """
        if self.reload is None:
            return False
        latest = self.reload()
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
