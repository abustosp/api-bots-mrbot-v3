"""Autorización RBAC por permisos evaluables (plan 02 §4.7).

Los permisos se guardan como datos (rol -> conjunto de permisos), nunca como
``if username == ...``. La autorización se evalúa después de autenticar y
antes de cargar datos sensibles; cada denegación administrativa debe
auditarse en la capa que la emite.
"""

from __future__ import annotations

from central_api.security.principals import ApiPrincipal

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "api_client": frozenset(
        {"jobs:create", "jobs:read_own", "jobs:cancel_own", "account:read"}
    ),
    "tenant_admin": frozenset(
        {"jobs:create", "jobs:read_own", "jobs:cancel_own", "account:read",
         "members:manage", "api_keys:manage"}
    ),
    "support_read": frozenset({"jobs:read_any", "users:read", "fleet:read"}),
    "support_operator": frozenset(
        {"jobs:read_any", "users:read", "fleet:read",
         "jobs:cancel_any", "workers:drain"}
    ),
    "billing_manager": frozenset({"billing:read", "billing:adjust"}),
    "security_admin": frozenset(
        {"api_keys:revoke_any", "audit:read", "policies:manage"}
    ),
    "super_admin": frozenset({"*"}),
}

# ``support_read`` no cancela; ``billing_manager`` no toca credenciales
# fiscales: se expresa negando permisos que esos roles no poseen.
CANCEL_ANY_ROLES = frozenset({"support_operator", "super_admin"})
FISCAL_CREDENTIAL_ROLES = frozenset({"super_admin"})


class AuthorizationError(PermissionError):
    """El principal autenticado carece del permiso exigido (403 público)."""


def principal_has(principal: ApiPrincipal, permission: str) -> bool:
    """Evalúa un permiso contra los scopes del principal (``*`` lo cubre todo)."""
    return "*" in principal.scopes or permission in principal.scopes


def require_permission(principal: ApiPrincipal, permission: str) -> None:
    """Exige un permiso o lanza ``AuthorizationError`` (sin detalle interno)."""
    if not principal_has(principal, permission):
        raise AuthorizationError(f"permiso requerido: {permission}")


def can_cancel(principal: ApiPrincipal, owner_user_id: str) -> bool:
    """Dueño con ``jobs:cancel_own`` o rol con ``jobs:cancel_any``."""
    if principal.user_id == owner_user_id and principal_has(
        principal, "jobs:cancel_own"
    ):
        return True
    return principal_has(principal, "jobs:cancel_any")


def can_read_job(principal: ApiPrincipal, owner_user_id: str) -> bool:
    """Dueño con ``jobs:read_own`` o rol con ``jobs:read_any``."""
    if principal.user_id == owner_user_id and principal_has(
        principal, "jobs:read_own"
    ):
        return True
    return principal_has(principal, "jobs:read_any")


__all__ = [
    "ROLE_PERMISSIONS",
    "CANCEL_ANY_ROLES",
    "FISCAL_CREDENTIAL_ROLES",
    "AuthorizationError",
    "principal_has",
    "require_permission",
    "can_cancel",
    "can_read_job",
]
