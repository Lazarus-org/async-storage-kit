"""Storage backends; optional third-party imports are lazy."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .filesystem import FileSystemStorage
    from .s3 import S3FileMetadata, S3Storage

from .memory import MemoryStorage

__all__ = ["FileSystemStorage", "MemoryStorage", "S3FileMetadata", "S3Storage"]


def __getattr__(name: str) -> object:
    if name == "FileSystemStorage":
        from .filesystem import FileSystemStorage

        return FileSystemStorage
    if name in {"S3Storage", "S3FileMetadata"}:
        from .s3 import S3FileMetadata, S3Storage

        return {"S3Storage": S3Storage, "S3FileMetadata": S3FileMetadata}[name]
    raise AttributeError(name)
