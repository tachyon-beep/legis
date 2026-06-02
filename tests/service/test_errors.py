import pytest

from legis.service.errors import (
    AuditIntegrityError,
    NotEnabledError,
    NotFoundError,
    ServiceError,
)


def test_all_service_errors_are_serviceerror_subclasses():
    for cls in (AuditIntegrityError, NotEnabledError, NotFoundError):
        assert issubclass(cls, ServiceError)


def test_service_error_carries_a_message():
    err = NotEnabledError("protected cell not enabled")
    assert str(err) == "protected cell not enabled"


def test_subclass_is_caught_as_service_error():
    with pytest.raises(ServiceError):
        raise AuditIntegrityError("tampered")
