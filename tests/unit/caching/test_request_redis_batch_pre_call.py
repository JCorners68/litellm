"""One Redis pipeline per backend for the pre-call reads a request makes: rate limiter Lua groups, the
router's cooldown and usage read, auth identity and spend counters all join the request batch."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from litellm import Router
from litellm.caching.dual_cache import DualCache
from litellm.caching.redis_batch import active_request_redis_batches, request_redis_batch_scope
from litellm.proxy.hooks.parallel_request_limiter_v3 import (
    CHECK_AND_INCREMENT_BY_N_SCRIPT,
    _PROXY_MaxParallelRequestsHandler_v3,
)
from litellm.proxy.utils import InternalUsageCache
from litellm.router_utils.cooldown_cache import CooldownCache
from litellm.router_utils.routing_read_batch import RoutingPrefetch

from .test_redis_batch import FakeClient, FakeRedisCache

_MODEL_GROUP = "claude"


def sha_of(script: str) -> str:
    return hashlib.sha1(script.encode()).hexdigest()  # noqa: S324


def _limiter(redis_cache: FakeRedisCache) -> _PROXY_MaxParallelRequestsHandler_v3:
    dual_cache = DualCache()
    limiter = _PROXY_MaxParallelRequestsHandler_v3(internal_usage_cache=InternalUsageCache(dual_cache=dual_cache))
    dual_cache.attach_redis_cache(redis_cache)  # after init: the fake has no server to register scripts on
    limiter.check_and_increment_by_n_script = AsyncMock(
        side_effect=AssertionError("descriptor groups must ride the request pipeline")
    )
    return limiter


def _descriptor(key: str, value: str, rpm: int) -> dict[str, Any]:
    return {"key": key, "value": value, "rate_limit": {"requests_per_unit": rpm}}


def _lua_ok_replies(command: tuple[Any, ...]) -> Any:
    if command[0] == "EVALSHA":
        return [0, 1, 1700000000]  # OK: one counter, new_counter=1, window_start
    if command[0] == "MGET":
        return [None for _ in command[1:]]
    raise AssertionError(command)


@pytest.mark.asyncio
async def test_descriptor_lua_calls_share_one_pipeline_and_each_keeps_its_result():
    client = FakeClient(_lua_ok_replies)
    limiter = _limiter(FakeRedisCache(client))
    descriptors = [
        _descriptor("api_key", "k1", 10),
        _descriptor("model_per_key", "k1:gpt", 5),
        _descriptor("team", "t1", 20),
    ]

    with request_redis_batch_scope():
        response = await limiter.atomic_check_and_increment_by_n(
            descriptors=descriptors,  # type: ignore[arg-type]
            increments=[{"requests": 1}, {"requests": 1}, {"requests": 1}],
        )

    assert response["overall_code"] == "OK"
    assert [s["descriptor_key"] for s in response["statuses"]] == ["api_key", "model_per_key", "team"]
    assert len(client.pipelines) == 1
    evalshas = [c for c in client.pipelines[0].commands if c[0] == "EVALSHA"]
    assert len(evalshas) == 3
    assert {c[1] for c in evalshas} == {sha_of(CHECK_AND_INCREMENT_BY_N_SCRIPT)}
    assert [c[3] for c in evalshas] == ["{api_key:k1}:window", "{model_per_key:k1:gpt}:window", "{team:t1}:window"]


@pytest.mark.asyncio
async def test_an_over_limit_descriptor_in_the_pipeline_refunds_the_groups_that_were_applied():
    def replies(command: tuple[Any, ...]) -> Any:
        if command[0] == "EVALSHA" and command[3] == "{team:t1}:window":
            return [1, 1, 21, 20]  # OVER_LIMIT on its first counter
        return _lua_ok_replies(command)

    client = FakeClient(replies)
    limiter = _limiter(FakeRedisCache(client))
    refunded: list[list[str]] = []

    async def _refund(applied):
        refunded.append([m["counter_key"] for group in applied for m in group])

    limiter._refund_applied_descriptor_groups = _refund  # type: ignore[method-assign]

    with request_redis_batch_scope():
        response = await limiter.atomic_check_and_increment_by_n(
            descriptors=[_descriptor("api_key", "k1", 10), _descriptor("team", "t1", 20)],  # type: ignore[arg-type]
            increments=[{"requests": 1}, {"requests": 1}],
        )

    assert response["overall_code"] == "OVER_LIMIT"
    assert response["statuses"][0]["descriptor_key"] == "team"
    assert refunded == [["{api_key:k1}:requests"]]
    assert len(client.pipelines) == 1


@pytest.mark.asyncio
async def test_a_pipeline_failure_refunds_nothing_and_falls_back_to_in_memory_enforcement():
    client = FakeClient(_lua_ok_replies, fail=ConnectionError("redis down"))
    limiter = _limiter(FakeRedisCache(client))

    with request_redis_batch_scope():
        response = await limiter.atomic_check_and_increment_by_n(
            descriptors=[_descriptor("api_key", "k1", 10), _descriptor("team", "t1", 20)],  # type: ignore[arg-type]
            increments=[{"requests": 1}, {"requests": 1}],
        )

    assert response["overall_code"] == "OK"
    assert len(response["statuses"]) == 2
    assert len(client.pipelines) == 1


@pytest.mark.asyncio
async def test_without_a_request_scope_descriptor_groups_run_the_script_directly_as_before():
    client = FakeClient(_lua_ok_replies)
    limiter = _limiter(FakeRedisCache(client))
    limiter.check_and_increment_by_n_script = AsyncMock(return_value=[0, 1, 1700000000])

    response = await limiter.atomic_check_and_increment_by_n(
        descriptors=[_descriptor("api_key", "k1", 10), _descriptor("team", "t1", 20)],  # type: ignore[arg-type]
        increments=[{"requests": 1}, {"requests": 1}],
    )

    assert response["overall_code"] == "OK"
    assert limiter.check_and_increment_by_n_script.await_count == 2
    assert client.pipelines == []


def _deployment(deployment_id: str) -> dict:
    return {
        "model_name": _MODEL_GROUP,
        "litellm_params": {"model": "anthropic/claude-x", "api_key": "test", "mock_response": "pong"},
        "model_info": {"id": deployment_id},
    }


def _router(redis_cache: FakeRedisCache) -> Router:
    router = Router(model_list=[_deployment("dep-a"), _deployment("dep-b")], routing_strategy="usage-based-routing-v2")
    router._update_redis_cache(cache=redis_cache)
    return router


@pytest.mark.asyncio
async def test_armed_routing_read_rides_the_admission_pipeline_and_routing_issues_no_read_of_its_own():
    client = FakeClient(_lua_ok_replies)
    redis_cache = FakeRedisCache(client)
    router = _router(redis_cache)
    limiter = _limiter(redis_cache)

    with request_redis_batch_scope():
        router.arm_routing_read_prefetch(_MODEL_GROUP, {})
        await limiter.atomic_check_and_increment_by_n(
            descriptors=[_descriptor("api_key", "k1", 10), _descriptor("team", "t1", 20)],  # type: ignore[arg-type]
            increments=[{"requests": 1}, {"requests": 1}],
        )
        deployment = await router.async_get_available_deployment(
            model=_MODEL_GROUP, messages=[{"role": "user", "content": "ping"}], request_kwargs={}
        )

    assert deployment["model_info"]["id"] in {"dep-a", "dep-b"}
    assert len(client.pipelines) == 1
    commands = client.pipelines[0].commands
    assert [c[0] for c in commands] == ["MGET", "EVALSHA", "EVALSHA"]
    mget_keys = set(commands[0][1:])
    assert {CooldownCache.get_cooldown_cache_key("dep-a"), CooldownCache.get_cooldown_cache_key("dep-b")} <= mget_keys
    assert any(":tpm:" in key for key in mget_keys) and any(":rpm:" in key for key in mget_keys)
    assert redis_cache.alone == []


@pytest.mark.asyncio
async def test_a_prefetch_that_does_not_cover_the_routing_keys_is_ignored_and_routing_reads_itself():
    client = FakeClient(_lua_ok_replies)
    redis_cache = FakeRedisCache(client)
    router = _router(redis_cache)

    with request_redis_batch_scope() as request:
        router.arm_routing_read_prefetch(_MODEL_GROUP, {})
        armed = request.prefetched["routing_read"]
        assert isinstance(armed, RoutingPrefetch)
        request.prefetched["routing_read"] = RoutingPrefetch(keys=frozenset({"other"}), result=armed.result)
        deployment = await router.async_get_available_deployment(
            model=_MODEL_GROUP, messages=[{"role": "user", "content": "ping"}], request_kwargs={}
        )
        assert request.prefetched == {}

    assert deployment["model_info"]["id"] in {"dep-a", "dep-b"}
    assert len(redis_cache.alone) == 1  # the shared cooldown+usage read, one round trip as in P1


@pytest.mark.asyncio
async def test_a_failed_prefetch_falls_back_to_the_shared_read():
    client = FakeClient(_lua_ok_replies, fail=ConnectionError("redis down"))
    redis_cache = FakeRedisCache(client)
    router = _router(redis_cache)

    with request_redis_batch_scope():
        router.arm_routing_read_prefetch(_MODEL_GROUP, {})
        deployment = await router.async_get_available_deployment(
            model=_MODEL_GROUP, messages=[{"role": "user", "content": "ping"}], request_kwargs={}
        )

    assert deployment["model_info"]["id"] in {"dep-a", "dep-b"}
    assert len(redis_cache.alone) == 1


@pytest.mark.asyncio
async def test_arming_is_a_no_op_for_a_strategy_without_usage_reads_and_outside_a_scope():
    redis_cache = FakeRedisCache(FakeClient(_lua_ok_replies))
    router = _router(redis_cache)
    router.arm_routing_read_prefetch(_MODEL_GROUP, {})
    assert active_request_redis_batches() is None

    shuffle = Router(model_list=[_deployment("dep-a")], routing_strategy="simple-shuffle")
    shuffle._update_redis_cache(cache=redis_cache)
    with request_redis_batch_scope() as request:
        shuffle.arm_routing_read_prefetch(_MODEL_GROUP, {})
        assert request.prefetched == {}


@pytest.mark.asyncio
async def test_two_backends_flush_concurrently_one_pipeline_each():
    a_client, b_client = FakeClient(_lua_ok_replies), FakeClient(_lua_ok_replies)
    a, b = FakeRedisCache(a_client), FakeRedisCache(b_client)
    with request_redis_batch_scope() as request:
        ra = request.batch(a).mget(["x", "y"])
        rb = request.batch(b).mget(["x"])
        await asyncio.gather(ra, rb)
    assert len(a_client.pipelines) == 1 and len(b_client.pipelines) == 1
