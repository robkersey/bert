"""Test-case procedure registry.

Procedures are looked up by the string in ``TestCase.procedure``. Generic
procedures live under ``bert.runner.procedures``; profile-specific ones
live alongside the profile's YAML (e.g. ``bert.profiles.heart_rate.tests``).

Third-party profiles can plug in via the ``bert.procedures`` entry point
group declared in ``pyproject.toml``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bert.runner.context import TestContext

# A procedure: ``async def proc(ctx: TestContext) -> None:`` — raises on failure.
Procedure = Callable[["TestContext"], Awaitable[None]]


_REGISTRY: dict[str, Procedure] = {}
_LOADED = False


def testcase(name: str) -> Callable[[Procedure], Procedure]:
    """Decorator: register ``func`` as the procedure named ``name``."""

    def _decorate(func: Procedure) -> Procedure:
        if name in _REGISTRY and _REGISTRY[name] is not func:
            raise RuntimeError(f"duplicate procedure registration: {name!r}")
        _REGISTRY[name] = func
        return func

    return _decorate


def get(name: str) -> Procedure:
    _ensure_loaded()
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "<none registered>"
        raise KeyError(
            f"no procedure registered as {name!r}. Known procedures: {known}"
        ) from exc


def all_names() -> list[str]:
    _ensure_loaded()
    return sorted(_REGISTRY)


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    # Always-loaded built-ins.
    importlib.import_module("bert.runner.procedures")

    # Entry-point-published procedures (incl. our own profile suites).
    try:
        eps = importlib.metadata.entry_points(group="bert.procedures")
    except TypeError:  # pragma: no cover  -- old importlib.metadata
        eps = importlib.metadata.entry_points().get("bert.procedures", [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            register = ep.load()
        except Exception as exc:  # pragma: no cover
            # Don't let a broken third-party plugin take down the runner.
            import warnings

            warnings.warn(f"failed to load procedure entry-point {ep.name}: {exc}", stacklevel=2)
            continue
        if callable(register):
            register()  # plugin's register() should call @testcase decorators
