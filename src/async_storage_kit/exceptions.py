"""Stable exception hierarchy used by all storage implementations."""


class StorageError(Exception):
    """Base class for errors raised by the storage library."""


class SuspiciousFileOperation(StorageError, ValueError):
    """A storage key is unsafe or escapes its configured namespace."""


class FileNotFound(StorageError, FileNotFoundError):
    """The requested storage key does not exist."""


class FileAlreadyExists(StorageError, FileExistsError):
    """The requested key already exists and overwriting is forbidden."""


class InvalidStorageError(StorageError, ValueError):
    """Invalid backend configuration or unknown storage alias."""


class UnsupportedOperation(StorageError, NotImplementedError):
    """A backend does not implement an optional storage capability."""
