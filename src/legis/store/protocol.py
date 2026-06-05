"""Store protocols consumed by governance core modules."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
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

    def transaction(self) -> AbstractContextManager[None]:
        """Group appends into one all-or-nothing transaction.

        Appends only. A read issued inside this context (``read_all``,
        ``read_by_seq``, ``verify_integrity``) is NOT guaranteed to observe
        uncommitted appends from the same batch — it sees a pre-batch snapshot
        — and on a single-connection backend (SQLite) may contend with the
        held write transaction. Resolve all reads before opening the batch.
        """
        ...
