"""Domain exceptions for the service layer.

Adapters switch on the exception *type*, never on message text. The HTTP
adapter maps these to status codes; the MCP adapter maps them to ``isError``
result envelopes (WP-M3).
"""

class ServiceError(RuntimeError):
    """Base for every governance service error."""


class AuditIntegrityError(ServiceError):
    """A verified trail failed tamper verification — non-retryable.

    HTTP maps this to 500; MCP maps it to ``error_code: AUDIT_INTEGRITY_FAILURE``.
    """


class NotEnabledError(ServiceError):
    """A required gate/dependency is not wired on this deployment."""


class NotFoundError(ServiceError):
    """A referenced resource (record, request, PR) does not exist."""
