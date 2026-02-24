from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import permutations
from typing import Any

import doeff_vm

from doeff import Eval, Delegate, EffectBase, Pass, Pure, Resume, WithHandler, default_handlers, do, run

EDGE_URL_TO_LOCAL = "url_to_local"
EDGE_LOCAL_TO_PROVIDER = "local_to_provider"
EDGE_URL_DIRECT = "url_direct"


@dataclass(frozen=True)
class Backtrack:
    """Value-level backtracking marker carried through the continuation chain."""

    reason: str


@dataclass(frozen=True)
class AssetLocation:
    uri: str
    asset_type: str


class Choose(EffectBase):
    def __init__(
        self,
        *,
        asset: AssetLocation,
        target: str,
        then: Callable[[AssetLocation], Any],
    ) -> None:
        super().__init__()
        self.asset = asset
        self.target = target
        self.then = then


class Resolve(EffectBase):
    def __init__(self, *, asset: AssetLocation, target: str) -> None:
        super().__init__()
        self.asset = asset
        self.target = target


class MockConvert(EffectBase):
    def __init__(self, *, edge_name: str, asset: AssetLocation, target_type: str) -> None:
        super().__init__()
        self.edge_name = edge_name
        self.asset = asset
        self.target_type = target_type


@dataclass
class MockConversionRuntime:
    conversions: dict[tuple[str, str, str], AssetLocation]
    forced_failures: set[tuple[str, str, str]] = field(default_factory=set)
    attempts: list[tuple[str, str, str]] = field(default_factory=list)
    failures: list[tuple[str, str, str]] = field(default_factory=list)

    def handler(self, effect: Any, k: Any):
        if not isinstance(effect, MockConvert):
            yield Pass()
            return

        key = (effect.edge_name, effect.asset.uri, effect.target_type)
        self.attempts.append(key)

        if key in self.forced_failures:
            self.failures.append(key)
            return (yield Resume(k, Backtrack(f"forced conversion failure: {key}")))

        converted = self.conversions.get(key)
        if converted is None:
            self.failures.append(key)
            return (yield Resume(k, Backtrack(f"missing conversion: {key}")))

        return (yield Resume(k, converted))


def _coerce_eval_handlers(handlers: Sequence[Any]) -> list[Any]:
    coerced: list[Any] = []
    for handler in handlers:
        if isinstance(handler, (doeff_vm.RustHandler, doeff_vm.DoeffGeneratorFn)):
            coerced.append(handler)
            continue
        if callable(handler):
            # Eval requires VM-native handlers, so we coerce via the validated WithHandler path.
            coerced.append(WithHandler(handler, Pure(None)).handler)
            continue
        coerced.append(handler)
    return coerced


def _wrap_with_handlers(program: Any, handlers_outer_to_inner: Sequence[Any]) -> Any:
    wrapped = program
    for handler in reversed(tuple(handlers_outer_to_inner)):
        wrapped = WithHandler(handler, wrapped)
    return wrapped


def _run_resolve(
    start: AssetLocation,
    target: str,
    *,
    edge_order_outer_to_inner: Sequence[str],
    runtime: MockConversionRuntime,
):
    eval_handlers: list[Any] = []

    def choose_backtrack_handler(effect: Any, _k: Any):
        if isinstance(effect, Choose):
            return (
                yield Resume(
                    _k,
                    Backtrack(
                        f"no edge handled {effect.asset.asset_type}->{effect.target} "
                        f"({effect.asset.uri})"
                    ),
                )
            )
        yield Pass()

    def resolver_handler(effect: Any, k: Any):
        if not isinstance(effect, Resolve):
            yield Pass()
            return

        @do
        def resolve(asset: AssetLocation, requested_target: str):
            if asset.asset_type == requested_target:
                return asset

            @do
            def then(intermediate: AssetLocation):
                return (yield Resolve(asset=intermediate, target=requested_target))

            return (yield Choose(asset=asset, target=requested_target, then=then))

        resolved = yield resolve(effect.asset, effect.target)
        return (yield Resume(k, resolved))

    def edge_url_direct(effect: Any, k: Any):
        if not isinstance(effect, Choose):
            yield Pass()
            return
        if effect.asset.asset_type != "url":
            yield Pass()
            return

        delegated = yield Delegate()
        if not isinstance(delegated, Backtrack):
            return (yield Resume(k, delegated))

        converted = yield MockConvert(
            edge_name=EDGE_URL_DIRECT,
            asset=effect.asset,
            target_type=effect.target,
        )
        if isinstance(converted, Backtrack):
            return (yield Resume(k, converted))

        resolved = yield Eval(effect.then(converted), eval_handlers)
        return (yield Resume(k, resolved))

    def edge_local_to_provider(effect: Any, k: Any):
        if not isinstance(effect, Choose):
            yield Pass()
            return
        if effect.asset.asset_type != "local_path":
            yield Pass()
            return

        delegated = yield Delegate()
        if not isinstance(delegated, Backtrack):
            return (yield Resume(k, delegated))

        converted = yield MockConvert(
            edge_name=EDGE_LOCAL_TO_PROVIDER,
            asset=effect.asset,
            target_type="provider_uri",
        )
        if isinstance(converted, Backtrack):
            return (yield Resume(k, converted))

        resolved = yield Eval(effect.then(converted), eval_handlers)
        return (yield Resume(k, resolved))

    def edge_url_to_local(effect: Any, k: Any):
        if not isinstance(effect, Choose):
            yield Pass()
            return
        if effect.asset.asset_type != "url":
            yield Pass()
            return

        delegated = yield Delegate()
        if not isinstance(delegated, Backtrack):
            return (yield Resume(k, delegated))

        converted = yield MockConvert(
            edge_name=EDGE_URL_TO_LOCAL,
            asset=effect.asset,
            target_type="local_path",
        )
        if isinstance(converted, Backtrack):
            return (yield Resume(k, converted))

        resolved = yield Eval(effect.then(converted), eval_handlers)
        return (yield Resume(k, resolved))

    edges_by_name = {
        EDGE_URL_TO_LOCAL: edge_url_to_local,
        EDGE_LOCAL_TO_PROVIDER: edge_local_to_provider,
        EDGE_URL_DIRECT: edge_url_direct,
    }
    ordered_edges = [edges_by_name[name] for name in edge_order_outer_to_inner]

    handlers_outer_to_inner = [
        runtime.handler,
        choose_backtrack_handler,
        *ordered_edges,
        resolver_handler,
    ]
    eval_handlers.extend(_coerce_eval_handlers(handlers_outer_to_inner))

    @do
    def program():
        return (yield Resolve(asset=start, target=target))

    wrapped = _wrap_with_handlers(program(), handlers_outer_to_inner)
    return run(wrapped, handlers=default_handlers())


def _sample_assets() -> tuple[AssetLocation, AssetLocation, AssetLocation]:
    url = AssetLocation(uri="https://assets.example/video.mp4", asset_type="url")
    local = AssetLocation(uri="/tmp/video.mp4", asset_type="local_path")
    provider = AssetLocation(uri="provider://bucket/video.mp4", asset_type="provider_uri")
    return url, local, provider


def test_choose_then_and_pass_allow_next_handler_fallback() -> None:
    url, local, _provider = _sample_assets()
    runtime = MockConversionRuntime(
        conversions={
            (EDGE_URL_TO_LOCAL, url.uri, "local_path"): local,
        }
    )

    result = _run_resolve(
        url,
        "local_path",
        edge_order_outer_to_inner=(EDGE_URL_DIRECT, EDGE_URL_TO_LOCAL, EDGE_LOCAL_TO_PROVIDER),
        runtime=runtime,
    )

    assert result.is_ok(), result.error
    assert result.value == local
    assert runtime.attempts == [
        (EDGE_URL_DIRECT, url.uri, "local_path"),
        (EDGE_URL_TO_LOCAL, url.uri, "local_path"),
    ]


def test_multi_hop_url_to_local_to_provider_resolves_via_recursive_choose_eval() -> None:
    url, local, provider = _sample_assets()
    runtime = MockConversionRuntime(
        conversions={
            (EDGE_URL_TO_LOCAL, url.uri, "local_path"): local,
            (EDGE_LOCAL_TO_PROVIDER, local.uri, "provider_uri"): provider,
        }
    )

    result = _run_resolve(
        url,
        "provider_uri",
        edge_order_outer_to_inner=(EDGE_URL_TO_LOCAL, EDGE_LOCAL_TO_PROVIDER, EDGE_URL_DIRECT),
        runtime=runtime,
    )

    assert result.is_ok(), result.error
    assert result.value == provider
    assert runtime.attempts == [
        (EDGE_URL_TO_LOCAL, url.uri, "local_path"),
        (EDGE_LOCAL_TO_PROVIDER, local.uri, "provider_uri"),
    ]


def test_working_path_found_for_all_edge_handler_orders() -> None:
    url, local, provider = _sample_assets()
    edges = (EDGE_URL_TO_LOCAL, EDGE_LOCAL_TO_PROVIDER, EDGE_URL_DIRECT)

    for order in permutations(edges):
        runtime = MockConversionRuntime(
            conversions={
                (EDGE_URL_TO_LOCAL, url.uri, "local_path"): local,
                (EDGE_LOCAL_TO_PROVIDER, local.uri, "provider_uri"): provider,
            }
        )
        result = _run_resolve(
            url,
            "provider_uri",
            edge_order_outer_to_inner=order,
            runtime=runtime,
        )
        assert result.is_ok(), f"order={order}: {result.error!r}"
        assert result.value == provider


def test_backtracking_after_mid_path_failure_tries_alternative_path() -> None:
    url = AssetLocation(uri="https://assets.example/fallback.mp4", asset_type="url")
    local_bad = AssetLocation(uri="/tmp/fallback-bad.mp4", asset_type="local_path")
    provider_direct = AssetLocation(
        uri="provider://bucket/fallback-direct.mp4",
        asset_type="provider_uri",
    )
    runtime = MockConversionRuntime(
        conversions={
            (EDGE_URL_TO_LOCAL, url.uri, "local_path"): local_bad,
            (EDGE_URL_DIRECT, url.uri, "provider_uri"): provider_direct,
        }
    )

    result = _run_resolve(
        url,
        "provider_uri",
        edge_order_outer_to_inner=(EDGE_URL_TO_LOCAL, EDGE_LOCAL_TO_PROVIDER, EDGE_URL_DIRECT),
        runtime=runtime,
    )

    assert result.is_ok(), result.error
    assert result.value == provider_direct
    assert runtime.attempts == [
        (EDGE_URL_TO_LOCAL, url.uri, "local_path"),
        (EDGE_LOCAL_TO_PROVIDER, local_bad.uri, "provider_uri"),
        (EDGE_URL_DIRECT, url.uri, "provider_uri"),
    ]
    assert runtime.failures == [
        (EDGE_LOCAL_TO_PROVIDER, local_bad.uri, "provider_uri"),
    ]


def test_edges_are_expressed_as_handlers_without_separate_edge_registry() -> None:
    url, local, provider = _sample_assets()

    missing_edges_runtime = MockConversionRuntime(
        conversions={
            (EDGE_URL_TO_LOCAL, url.uri, "local_path"): local,
            (EDGE_LOCAL_TO_PROVIDER, local.uri, "provider_uri"): provider,
        }
    )
    missing_edges_result = _run_resolve(
        url,
        "provider_uri",
        edge_order_outer_to_inner=(EDGE_URL_DIRECT,),
        runtime=missing_edges_runtime,
    )
    assert missing_edges_result.is_ok(), missing_edges_result.error
    assert isinstance(missing_edges_result.value, Backtrack)

    installed_edges_runtime = MockConversionRuntime(
        conversions={
            (EDGE_URL_TO_LOCAL, url.uri, "local_path"): local,
            (EDGE_LOCAL_TO_PROVIDER, local.uri, "provider_uri"): provider,
        }
    )
    installed_edges_result = _run_resolve(
        url,
        "provider_uri",
        edge_order_outer_to_inner=(EDGE_URL_TO_LOCAL, EDGE_LOCAL_TO_PROVIDER),
        runtime=installed_edges_runtime,
    )
    assert installed_edges_result.is_ok(), installed_edges_result.error
    assert installed_edges_result.value == provider
