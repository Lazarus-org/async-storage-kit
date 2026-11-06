"""Storage-key validation and filenames, independent from any backend."""

from __future__ import annotations

import os
import re
import secrets
import unicodedata
from pathlib import Path

from .exceptions import SuspiciousFileOperation

_DRIVE = re.compile(r"^[a-zA-Z]:")
_STAGING = re.compile(r"\.async-storage-[0-9a-f]{32}\.tmp\Z")


def normalize_name(name: str, *, allow_empty: bool = False) -> str:
    """Validate a relative POSIX-style storage key; do not silently collapse '..'.

    Backslashes are interpreted as separators for compatibility with Windows
    uploads. A storage key is *not* a URL; URL escaping belongs to the backend.
    """
    if not isinstance(name, str):
        raise SuspiciousFileOperation("Storage name must be a string.")
    if not name:
        if allow_empty:
            return ""
        raise SuspiciousFileOperation("Storage name must not be empty.")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise SuspiciousFileOperation("Storage name contains control characters.")
    if name.startswith(("/", "\\")) or _DRIVE.match(name):
        raise SuspiciousFileOperation("An absolute storage path is not allowed.")
    normalized = name.replace("\\", "/")
    components = normalized.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise SuspiciousFileOperation(
            "Empty, '.' and '..' path components are forbidden."
        )
    if any(_STAGING.fullmatch(component) for component in components):
        raise SuspiciousFileOperation("Temporary storage names are reserved.")
    return normalized


def normalize_filesystem_name(name: str, *, allow_empty: bool = False) -> str:
    """Reject Windows aliases and streams, consistently on every filesystem host."""
    name = normalize_name(name, allow_empty=allow_empty)
    for part in name.split("/") if name else []:
        stem = part.split(".", 1)[0].rstrip(" ").upper()
        if (
            any(char in '<>:"|?*' for char in part)
            or part.endswith((".", " "))
            or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem)
        ):
            raise SuspiciousFileOperation(
                "Filesystem name contains a reserved alias or character."
            )
    return name


def validate_file_name(name: str, allow_relative_path: bool = False) -> None:
    """Validate a filename or a relative key, raising on unsafe values."""
    normalized = normalize_name(name)
    if not allow_relative_path and "/" in normalized:
        raise SuspiciousFileOperation("Expected a filename, not a storage path.")


def get_valid_filename(name: str) -> str:
    """Convert a display filename to an ASCII-safe, portable filename."""
    name = unicodedata.normalize("NFKD", str(name).strip()).replace(" ", "_")
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^\w.\-]", "", name)
    if name in {"", ".", ".."}:
        raise SuspiciousFileOperation("Filename has no usable characters.")
    return name


def get_random_string(length: int = 7) -> str:
    if length < 1:
        raise ValueError("Length must be positive.")
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def alternative_name(name: str, *, max_length: int | None = None) -> str:
    """Return a randomized candidate, truncating the basename when necessary."""
    normalized = normalize_name(name)
    head, separator, filename = normalized.rpartition("/")
    # Keep compound extensions intact, matching the legacy naming behavior.
    import pathlib

    suffixes = pathlib.PurePosixPath(filename).suffixes
    ext = "".join(suffixes)
    stem = filename[: -len(ext)] if ext else filename
    suffix = "_" + get_random_string()
    prefix = head + separator
    if max_length is not None:
        available = max_length - len(prefix) - len(ext) - len(suffix)
        if available < 1:
            raise SuspiciousFileOperation(
                "max_length cannot accommodate a unique name."
            )
        stem = stem[:available]
    return f"{prefix}{stem}{suffix}{ext}"


def safe_join(base: str | os.PathLike[str], *paths: str) -> str:
    """Resolve a path and enforce directory containment (including existing links).

    This prevents ordinary traversal and symlink escapes at validation time.
    Race-free directory-descriptor-based containment is a later hardening step.
    """
    root = Path(base).resolve()
    candidate = root
    for part in paths:
        name = normalize_filesystem_name(part, allow_empty=True)
        if name:
            candidate = candidate.joinpath(*name.split("/"))
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SuspiciousFileOperation("File path escapes the storage root.") from exc
    return str(candidate)
