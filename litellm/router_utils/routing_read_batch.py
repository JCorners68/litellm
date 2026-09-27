"""
One Redis round trip for the reads a request needs before a deployment can be picked.

The cooldown filter (`CooldownCache`, its own `DualCache`) and usage-based selection
(`LowestTPMLoggingHandler_v2`, the router cache) each issue their own MGET because they live in
different objects. `RoutingReadBatch` fetches both key sets in one
`DualCache.async_batch_get_cache_shared` while the healthy deployments are being resolved and hands
the usage slice to the strategy, so selection does not read again.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from litellm._logging import verbose_router_logger
from litellm.caching.dual_cache import DualCache
from litellm.caching.redis_batch import BatchResult, active_request_redis_batches
from litellm.router_strategy.lowest_tpm_rpm_v2 import LowestTPMLoggingHandler_v2, PrefetchedUsage
from litellm.router_utils.cooldown_cache import CooldownCache

if TYPE_CHECKING:
    from opentelemetry.trace import Span as _Span

    from litellm.router import Router as _Router

    LitellmRouter = _Router
    Span = _Span
else:
    LitellmRouter = Any
    Span = Any


_PREFETCH_SLOT: Final = "routing_read"


@dataclass(frozen=True, slots=True)
class RoutingPrefetch:
    """The cooldown and usage keys of a model group, declared on the request's Redis batch before admission
    flushes it, so the routing read rides the same round trip as the rate limiter's Lua calls."""

    keys: frozenset[str]
    result: BatchResult[Mapping[str, object]]

    @staticmethod
    def arm(
        litellm_router_instance: LitellmRouter,
        usage_selector: LowestTPMLoggingHandler_v2,
        deployments: list,
    ) -> None:
        request: Final = active_request_redis_batches()
        redis_cache: Final = litellm_router_instance.cache.redis_cache
        if request is None or redis_cache is None or _PREFETCH_SLOT in request.prefetched:
            return
        cooldown_keys: Final = [
            CooldownCache.get_cooldown_cache_key(model_id) for model_id in litellm_router_instance.get_model_ids()
        ]
        tpm_keys, rpm_keys = usage_selector.usage_counter_keys(deployments)
        keys: Final = [*cooldown_keys, *tpm_keys, *rpm_keys]
        request.prefetched[_PREFETCH_SLOT] = RoutingPrefetch(
            keys=frozenset(keys), result=request.batch(redis_cache).mget(keys)
        )

    @staticmethod
    def take(needed: list[str]) -> "RoutingPrefetch | None":
        """The armed prefetch when it covers every key this read needs; taken once, so a retry reads fresh."""
        request: Final = active_request_redis_batches()
        if request is None:
            return None
        armed: Final = request.prefetched.pop(_PREFETCH_SLOT, None)
        if isinstance(armed, RoutingPrefetch) and armed.keys.issuperset(needed):
            return armed
        return None


class RoutingReadBatch:
    def __init__(self, usage_selector: LowestTPMLoggingHandler_v2) -> None:
        self.usage_selector: Final = usage_selector
        self.prefetched_usage: PrefetchedUsage | None = None

    @staticmethod
    def for_strategy(strategy: str | None, selector: object) -> "RoutingReadBatch | None":
        if strategy == "usage-based-routing-v2" and isinstance(selector, LowestTPMLoggingHandler_v2):
            return RoutingReadBatch(usage_selector=selector)
        return None

    async def async_get_cooldown_deployments(
        self,
        litellm_router_instance: LitellmRouter,
        healthy_deployments: list,
        parent_otel_span: Span | None,
    ) -> list[str]:
        """
        `_async_get_cooldown_deployments`, with the strategy's tpm/rpm counters for
        `healthy_deployments` fetched in the same MGET and kept as `prefetched_usage`.
        """
        model_ids: Final = litellm_router_instance.get_model_ids()
        cooldown_keys: Final = [CooldownCache.get_cooldown_cache_key(model_id) for model_id in model_ids]
        tpm_keys, rpm_keys = self.usage_selector.usage_counter_keys(healthy_deployments)
        usage_keys: Final = tpm_keys + rpm_keys

        reads: Final = [
            (litellm_router_instance.cooldown_cache.cooldown_store, cooldown_keys),
            (self.usage_selector.router_cache, usage_keys),
        ]
        cooldown_results, usage_values = await self._read_prefetched(
            reads
        ) or await DualCache.async_batch_get_cache_shared(reads, parent_otel_span=parent_otel_span)
        self.prefetched_usage = PrefetchedUsage(
            keys=frozenset(usage_keys),
            values=None if usage_values is None else dict(zip(usage_keys, usage_values)),
        )

        cooldown_models: Final = litellm_router_instance.cooldown_cache.active_cooldowns_from_results(
            model_ids, cooldown_results
        )
        verbose_router_logger.debug("retrieve cooldown models: %s", cooldown_models)
        return [model_id for model_id, _ in cooldown_models]

    @staticmethod
    async def _read_prefetched(
        reads: list[tuple[DualCache, list[str]]],
    ) -> list[list[object | None] | None] | None:
        """Serve the reads from the request's armed `RoutingPrefetch`, backfilling each cache's memory tier as
        its own batch read would. None when nothing usable was armed or the prefetch failed."""
        prefetch: Final = RoutingPrefetch.take([key for _, keys in reads for key in keys])
        if prefetch is None:
            return None
        try:
            values: Final = await prefetch.result
        except Exception as e:  # noqa: BLE001  # the shared read below applies the caches' own Redis fallback
            verbose_router_logger.debug("routing prefetch failed, reading again: %s", e)
            return None
        results: Final[list[list[object | None] | None]] = []
        for cache, keys in reads:
            pending = await cache._prepare_batch_get(keys, local_only=True)  # pyright: ignore[reportPrivateUsage]  # same two-step read as async_batch_get_cache_shared
            results.append(await cache._apply_batch_get(pending, {key: values.get(key) for key in keys}))  # pyright: ignore[reportPrivateUsage]
        return results
