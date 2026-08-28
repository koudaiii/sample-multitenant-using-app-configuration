"""Tenant registry and identifier validation.

Every untrusted tenant identifier must pass through `TenantRegistry.resolve`
before it reaches a configuration store. A raw identifier interpolated into a
key filter would let a caller read another tenant's settings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")


class UnknownTenantError(LookupError):
    """The identifier is malformed, or names no registered tenant."""


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    display_name: str


class TenantRegistry:
    """The set of tenants this deployment serves."""

    def __init__(self, tenants: Iterable[Tenant]) -> None:
        self._tenants: dict[str, Tenant] = {}
        for tenant in tenants:
            if not TENANT_ID_PATTERN.match(tenant.tenant_id):
                raise ValueError(f"invalid tenant id in registry: {tenant.tenant_id!r}")
            self._tenants[tenant.tenant_id] = tenant

    def __iter__(self) -> Iterator[Tenant]:
        return iter(self._tenants.values())

    def __len__(self) -> int:
        return len(self._tenants)

    def ids(self) -> list[str]:
        return list(self._tenants)

    def resolve(self, raw: str) -> Tenant:
        """Validate an untrusted identifier and return the registered tenant.

        The pattern check is redundant with the registry lookup on its own, but
        both are kept: the pattern documents the contract, and a future source
        that builds queries from the identifier stays safe by construction.
        """
        if not isinstance(raw, str) or not TENANT_ID_PATTERN.match(raw):
            raise UnknownTenantError(raw)
        try:
            return self._tenants[raw]
        except KeyError:
            raise UnknownTenantError(raw) from None
