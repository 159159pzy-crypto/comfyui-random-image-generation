"""Natural-language generation integration for Anima Random Studio."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "NaturalEngine",
    "NaturalEngineError",
    "NaturalJobManager",
    "ProviderRegistry",
    "ProviderRegistryError",
]


_EXPORTS = {
    "NaturalEngine": (".engine", "NaturalEngine"),
    "NaturalEngineError": (".engine", "NaturalEngineError"),
    "NaturalJobManager": (".jobs", "NaturalJobManager"),
    "ProviderRegistry": (".providers", "ProviderRegistry"),
    "ProviderRegistryError": (".providers", "ProviderRegistryError"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
