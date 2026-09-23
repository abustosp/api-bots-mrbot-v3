"""Principal autenticado y frontera de seguridad (plan 02 §4.1).

La autenticación termina antes de cualquier servicio de dominio: el router
recibe un ``ApiPrincipal`` inmutable y nunca un ``user_id`` provisto por el
cliente. Las fases con PostgreSQL resolverán el principal contra ``api_keys``
y ``users``; este esqueleto deriva un principal estable del ``key_id``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApiPrincipal:
    """Identidad de cliente ya autenticada para un request."""

    user_id: str
    key_id: str
    scopes: frozenset[str] = frozenset(
        {"jobs:create", "jobs:read_own", "jobs:cancel_own", "account:read"}
    )
    tenant: str = "default"

    def has(self, scope: str) -> bool:
        """Indica si el principal posee el permiso indicado."""
        return scope in self.scopes


ANONYMOUS_USER_ID = "dev-local-user"

__all__ = ["ApiPrincipal", "ANONYMOUS_USER_ID"]
