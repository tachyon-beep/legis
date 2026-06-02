"""Transport-agnostic governance service layer.

The decision logic that both the HTTP adapter (``legis.api.app``) and the MCP
adapter (``legis.mcp``, WP-M3) drive. Functions here raise ``ServiceError``
subclasses — never ``HTTPException`` and never a JSON-RPC error — so each
transport adapter owns its own error translation.
"""

from legis.service.errors import (
    AuditIntegrityError,
    NotEnabledError,
    NotFoundError,
    ServiceError,
)
from legis.service.governance import compute_override_rate, resolve_for_record, verified_records

__all__ = [
    "ServiceError",
    "AuditIntegrityError",
    "NotEnabledError",
    "NotFoundError",
    "compute_override_rate",
    "resolve_for_record",
    "verified_records",
]
