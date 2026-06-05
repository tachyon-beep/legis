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


class InvalidArgumentError(ServiceError):
    """Caller input is structurally valid for the transport but invalid for Legis."""


class ProtectedKeyRequiredError(ServiceError):
    """A protected trail was read without the HMAC key needed to verify it.

    Fail-closed: a trail carrying protected records cannot be scored without the
    key that proves it untampered (Q-H2 / 07cf54e). The cli gate maps this to a
    non-zero exit.
    """
