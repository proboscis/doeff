"""Production handlers for doeff-pinjected effects."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from doeff import Ask, Await, Effect, Pass, Resume, do
from doeff_pinjected.effects import PinjectedProvide, PinjectedResolve

ProtocolHandler = Callable[[Any, Any], Any]


class ResolverLike(Protocol):
    """Protocol for resolver objects consumed by production handlers."""

    def provide(self, key: Any) -> Any: ...


@dataclass
class _ProductionPinjectedRuntime:
    resolver: ResolverLike | None = None
    bindings: dict[Any, Any] = field(default_factory=dict)

    def resolve_resolver(self):
        if self.resolver is not None:
            return self.resolver

        resolved = yield Ask("__resolver__")
        if not hasattr(resolved, "provide"):
            raise TypeError("Pinjected resolver must define provide(key)")
        self.resolver = resolved
        return resolved

    def handle_resolve(self, effect: PinjectedResolve, k):
        if effect.key in self.bindings:
            return (yield Resume(k, self.bindings[effect.key]))

        resolved_resolver = yield from self.resolve_resolver()
        provided = resolved_resolver.provide(effect.key)
        if inspect.isawaitable(provided):
            provided = yield Await(provided)
        return (yield Resume(k, provided))

    def handle_provide(self, effect: PinjectedProvide, k):
        self.bindings[effect.key] = effect.value
        return (yield Resume(k, None))


def production_handlers(
    *,
    resolver: ResolverLike | None = None,
    bindings: Mapping[Any, Any] | None = None,
) -> ProtocolHandler:
    """Build a production protocol handler for pinjected bridge effects."""

    runtime = _ProductionPinjectedRuntime(
        resolver=resolver,
        bindings=dict(bindings or {}),
    )

    @do
    def handler(effect: Effect, k: Any):
        if isinstance(effect, PinjectedResolve):
            return (yield from runtime.handle_resolve(effect, k))
        if isinstance(effect, PinjectedProvide):
            return (yield from runtime.handle_provide(effect, k))
        return (yield Pass())

    return handler


__all__ = [
    "ProtocolHandler",
    "ResolverLike",
    "production_handlers",
]
