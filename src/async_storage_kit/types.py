"""Public protocol-level storage types."""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Literal, TypeAlias

Content: TypeAlias = bytes | bytearray | memoryview | BinaryIO | AsyncIterable[bytes]
ConflictPolicy: TypeAlias = Literal["rename", "overwrite", "error"]


@dataclass(frozen=True, slots=True)
class FileMetadata:
    """Metadata with a UTC-aware modification time."""

    name: str
    size: int
    modified_at: datetime
