"""Explicit, lazily populated storage registries."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import import_module
from threading import RLock
from typing import Any

from .base import StorageBackend
from .exceptions import InvalidStorageError

StorageFactory = Callable[[], StorageBackend]
StorageDefinition = StorageBackend | StorageFactory | Mapping[str, Any]


class StorageRegistry:
    """An application-scoped storage registry (no implicit project settings)."""

    def __init__(self, backends: Mapping[str, StorageDefinition] | None = None) -> None:
        self._backends: dict[str, StorageDefinition] = dict(backends or {})
        for alias in self._backends:
            self._validate_alias(alias)
        self._storages: dict[str, StorageBackend] = {}
        self._constructing: set[str] = set()
        self._lock = RLock()

    def __getitem__(self, alias: str) -> StorageBackend:
        self._validate_alias(alias)
        with self._lock:
            if alias in self._storages:
                return self._storages[alias]
            if alias not in self._backends:
                raise InvalidStorageError(f"Unknown storage alias: {alias!r}.")
            if alias in self._constructing:
                raise InvalidStorageError("Circular storage factory dependency.")
            self._constructing.add(alias)
            try:
                storage = self.create_storage(self._backends[alias])
            finally:
                self._constructing.remove(alias)
            self._storages[alias] = storage
            return storage

    def register(
        self, alias: str, backend: StorageDefinition, *, replace: bool = False
    ) -> None:
        self._validate_alias(alias)
        with self._lock:
            if alias in self._backends and not replace:
                raise InvalidStorageError(
                    f"Storage alias {alias!r} is already registered."
                )
            self._backends[alias] = backend
            self._storages.pop(alias, None)

    def clear_cache(self) -> None:
        with self._lock:
            self._storages.clear()

    @staticmethod
    def _validate_alias(alias: str) -> None:
        if not isinstance(alias, str) or not alias.strip():
            raise InvalidStorageError("Storage alias must be a non-empty string.")

    @staticmethod
    def create_storage(definition: StorageDefinition) -> StorageBackend:
        if isinstance(definition, StorageBackend):
            return definition
        if isinstance(definition, Mapping):
            # Both new lower-case and old Django-style configuration are supported.
            backend = definition.get("backend", definition.get("BACKEND"))
            options = definition.get("options", definition.get("OPTIONS", {}))
            if not isinstance(options, Mapping):
                raise InvalidStorageError("Storage options must be a mapping.")
            if not all(isinstance(key, str) for key in options):
                raise InvalidStorageError("Storage option names must be strings.")
            if isinstance(backend, str):
                try:
                    module_name, attribute = backend.rsplit(".", 1)
                    factory = getattr(import_module(module_name), attribute)
                except (ImportError, AttributeError, ValueError) as exc:
                    raise InvalidStorageError(
                        f"Cannot load storage backend {backend!r}."
                    ) from exc
            elif callable(backend):
                factory = backend
            else:
                raise InvalidStorageError("Storage configuration requires a backend.")
            if not callable(factory):
                raise InvalidStorageError("Storage backend must be callable.")
            try:
                instance = factory(**dict(options))
            except (TypeError, ValueError):
                raise InvalidStorageError(
                    "Storage backend configuration is invalid. Check its options."
                ) from None
        elif callable(definition):
            try:
                instance = definition()
            except InvalidStorageError:
                raise
            except (TypeError, ValueError):
                raise InvalidStorageError(
                    "Storage factory configuration is invalid."
                ) from None
        else:
            raise InvalidStorageError(
                "Storage definition must be a backend, factory or mapping."
            )
        if not isinstance(instance, StorageBackend):
            raise InvalidStorageError(
                "Backend factory must return a StorageBackend instance."
            )
        return instance


class LazyStorageProxy:
    """Attribute proxy that resolves one alias on first use."""

    def __init__(self, registry: StorageRegistry, alias: str = "default") -> None:
        self._registry = registry
        self._alias = alias

    @property
    def backend(self) -> StorageBackend:
        return self._registry[self._alias]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backend, name)

    def __repr__(self) -> str:
        return f"<LazyStorageProxy alias={self._alias!r}>"


# Retains the familiar old name without depending on global settings.
StorageHandler = StorageRegistry
