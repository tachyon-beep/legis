"""Store protocols consumed by governance core modules."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class AuditRecordLike(Protocol):
    @property
    def seq(self) -> int: ...

    @property
    def payload(self) -> dict[str, Any]: ...

    @property
    def content_hash(self) -> str: ...

    @property
    def prev_hash(self) -> str: ...


class AppendOnlyStore(Protocol):
    def append(self, payload: dict[str, Any]) -> int: ...

    def read_all(self) -> Sequence[AuditRecordLike]: ...

    def read_by_seq(self, seq: int) -> AuditRecordLike | None: ...

    def verify_integrity(self) -> bool: ...
