"""Framework-agnostic async storage primitives."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .backends.filesystem import FileSystemStorage
    from .backends.s3 import S3FileMetadata, S3Storage

from .backends.memory import MemoryStorage
from .base import StorageBackend
from .exceptions import (
    FileAlreadyExists,
    FileNotFound,
    InvalidStorageError,
    StorageError,
    SuspiciousFileOperation,
    UnsupportedOperation,
)
from .protocols import AsyncStorage
from .registry import LazyStorageProxy, StorageRegistry
from .types import ConflictPolicy, Content, FileMetadata

__version__ = "1.0.0"

__all__ = [
    "AsyncStorage",
    "ConflictPolicy",
    "Content",
    "FileAlreadyExists",
    "FileMetadata",
    "FileNotFound",
    "FileSystemStorage",
    "InvalidStorageError",
    "LazyStorageProxy",
    "MemoryStorage",
    "S3FileMetadata",
    "S3Storage",
    "StorageBackend",
    "StorageError",
    "StorageRegistry",
    "SuspiciousFileOperation",
    "UnsupportedOperation",
]


def __getattr__(name: str) -> object:
    if name == "FileSystemStorage":
        from .backends.filesystem import FileSystemStorage

        return FileSystemStorage
    if name in {"S3Storage", "S3FileMetadata"}:
        from .backends.s3 import S3FileMetadata, S3Storage

        return {"S3Storage": S3Storage, "S3FileMetadata": S3FileMetadata}[name]
    raise AttributeError(name)
