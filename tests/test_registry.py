from __future__ import annotations

import pytest

from async_storage_kit import (
    InvalidStorageError,
    LazyStorageProxy,
    MemoryStorage,
    StorageRegistry,
)


def test_registry_is_lazy_and_cached():
    constructed = []

    def factory():
        constructed.append(True)
        return MemoryStorage()

    registry = StorageRegistry({"default": factory})
    assert constructed == []
    first = registry["default"]
    assert registry["default"] is first
    assert len(constructed) == 1
    registry.clear_cache()
    assert registry["default"] is not first
    assert len(constructed) == 2


def test_django_style_configuration_and_proxy():
    registry = StorageRegistry(
        {
            "default": {
                "BACKEND": "async_storage_kit.backends.memory.MemoryStorage",
                "OPTIONS": {},
            }
        }
    )
    assert isinstance(registry["default"], MemoryStorage)
    proxy = LazyStorageProxy(registry)
    assert proxy.backend is registry["default"]


def test_invalid_definitions():
    registry = StorageRegistry()
    with pytest.raises(InvalidStorageError):
        _ = registry["unconfigured"]
    with pytest.raises(InvalidStorageError):
        registry.register("", MemoryStorage())
    registry.register("a", MemoryStorage())
    with pytest.raises(InvalidStorageError):
        registry.register("a", MemoryStorage())
    with pytest.raises(InvalidStorageError):
        StorageRegistry.create_storage({"BACKEND": "missing.module.Storage"})
    with pytest.raises(InvalidStorageError):
        StorageRegistry.create_storage(lambda: object())


def test_can_replace_registered_backend():
    old = MemoryStorage()
    new = MemoryStorage()
    registry = StorageRegistry({"a": old})
    assert registry["a"] is old
    registry.register("a", new, replace=True)
    assert registry["a"] is new


@pytest.mark.parametrize("alias", [None, 1, "", "   "])
def test_aliases_are_validated_consistently(alias):
    with pytest.raises(InvalidStorageError):
        StorageRegistry({alias: MemoryStorage()})
    registry = StorageRegistry()
    with pytest.raises(InvalidStorageError):
        registry.register(alias, MemoryStorage())
    with pytest.raises(InvalidStorageError):
        _ = registry[alias]


def test_factory_error_does_not_expose_option_values():
    import traceback

    def factory(**options):
        raise ValueError("sensitive-value-from-options")

    for definition in ({"backend": factory, "options": {}}, factory):
        with pytest.raises(InvalidStorageError) as error:
            StorageRegistry.create_storage(definition)
        assert "sensitive-value-from-options" not in "".join(
            traceback.format_exception(error.value)
        )


def test_registry_rejects_recursive_factory_and_recovers():
    registry = StorageRegistry()
    registry.register("loop", lambda: registry["loop"])
    with pytest.raises(InvalidStorageError, match="Circular"):
        _ = registry["loop"]
    replacement = MemoryStorage()
    registry.register("loop", replacement, replace=True)
    assert registry["loop"] is replacement


@pytest.mark.parametrize(
    "definition",
    [
        {"backend": MemoryStorage, "options": {1: "value"}},
        {"backend": "async_storage_kit.__version__"},
        {"backend": MemoryStorage, "options": []},
    ],
)
def test_malformed_backend_options(definition):
    with pytest.raises(InvalidStorageError):
        StorageRegistry.create_storage(definition)
