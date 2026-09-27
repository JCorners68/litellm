from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

import pytest
from pydantic import ValidationError

from litellm import constants
from litellm.constants import PTU_SENTINEL_API_KEY
from litellm.repositories.daily_activity_repository import DailyActivityRepository
from litellm.repositories.daily_activity_sql import (
    ExportCursor,
    build_cache_leakage_keys_sql,
    build_export_sql,
    build_key_search_sql,
    build_model_top_keys_sql,
)
from litellm.types.repositories.daily_activity import (
    DailyActivityScope,
    DailyActivityTable,
    ExportType,
    KeyMetadataRow,
    SpendLogsWindow,
)


@dataclass(frozen=True, slots=True)
class _FakeVerificationToken:
    token: str
    key_alias: str | None
    team_id: str | None
    user_id: str | None
    metadata: object | None


@dataclass(frozen=True, slots=True)
class _FakeDeletedVerificationToken(_FakeVerificationToken):
    deleted_at: datetime


def _scope(
    *,
    table: DailyActivityTable = DailyActivityTable.USER,
    entity_ids: tuple[str, ...] | None = ("user-1",),
    api_keys: tuple[str, ...] | None = None,
) -> DailyActivityScope:
    entity_field: Final = "team_id" if table is DailyActivityTable.TEAM else "user_id"
    return DailyActivityScope(
        table=table,
        entity_id_field=entity_field,
        entity_ids=entity_ids,
        exclude_entity_ids=(),
        api_keys=api_keys,
        start_date="2026-01-01",
        end_date="2026-01-31",
        model=None,
        timezone_offset_minutes=None,
    )


def _key_spend_row(api_key: str) -> dict[str, object]:
    return {
        "api_key": api_key,
        "spend": 1.0,
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
        "api_requests": 1,
        "successful_requests": 1,
        "failed_requests": 0,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 1,
    }


def _export_row(api_key: str | None) -> dict[str, object]:
    return {
        "date": "2026-01-01",
        "entity_id": "user-1",
        "entity_alias": None,
        "api_key": api_key,
        "key_alias": None,
        "user_id": None,
        "user_email": None,
        "model": None,
        "spend": 1.0,
        "flat_cost": 0.0,
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "api_requests": 1,
        "successful_requests": 1,
        "failed_requests": 0,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 1,
    }


class _FakeTable:
    def __init__(self, rows: Sequence[object] = ()) -> None:
        self.rows: Final = tuple(rows)
        self.find_many_calls: list[Mapping[str, object]] = []
        self.count_calls: list[Mapping[str, object]] = []

    async def find_many(self, *, where: Mapping[str, object]) -> tuple[object, ...]:
        self.find_many_calls.append(where)
        if "token" not in where:
            return self.rows
        token_filter: Final = where["token"]
        if not isinstance(token_filter, Mapping):
            return ()
        token_values: Final = token_filter.get("in")
        if not isinstance(token_values, list):
            return ()
        return tuple(row for row in self.rows if isinstance(row, _FakeVerificationToken) and row.token in token_values)

    async def count(self, *, where: Mapping[str, object]) -> int:
        self.count_calls.append(where)
        return len(self.rows)


class _FakeDatabase:
    def __init__(self, responses: Sequence[Sequence[Mapping[str, object]] | None] = ()) -> None:
        self.responses = tuple(responses)
        self.query_calls: list[tuple[str, tuple[object, ...]]] = []
        self.litellm_verificationtoken = _FakeTable()
        self.litellm_deletedverificationtoken = _FakeTable()
        self.litellm_dailyuserspend = _FakeTable()
        self.litellm_dailyteamspend = _FakeTable()
        self.litellm_dailytagspend = _FakeTable()
        self.litellm_dailyorganizationspend = _FakeTable()
        self.litellm_dailyenduserspend = _FakeTable()
        self.litellm_dailyagentspend = _FakeTable()

    async def query_raw(self, query: str, *params: object) -> Sequence[Mapping[str, object]] | None:
        self.query_calls.append((query, params))
        response_index: Final = len(self.query_calls) - 1
        if response_index >= len(self.responses):
            return ()
        return self.responses[response_index]


class _FakePrismaClient:
    def __init__(self, database: _FakeDatabase) -> None:
        self.db: Final = database


class _ProxyReads:
    def __init__(self, marker: str | None = None, *, marker_error: bool = False) -> None:
        self.marker = marker
        self.marker_error = marker_error
        self.marker_calls = 0
        self.recovery_calls: list[tuple[Mapping[str, KeyMetadataRow], frozenset[str], SpendLogsWindow | None]] = []

    async def global_rollup_reconciled_through(self) -> str | None:
        self.marker_calls += 1
        if self.marker_error:
            raise RuntimeError("marker unavailable")
        return self.marker

    async def recover_key_metadata(
        self,
        resolved: Mapping[str, KeyMetadataRow],
        api_keys: frozenset[str],
        window: SpendLogsWindow | None,
    ) -> Mapping[str, KeyMetadataRow]:
        self.recovery_calls.append((resolved, api_keys, window))
        return resolved


def _repository(
    database: _FakeDatabase, proxy_reads: _ProxyReads | None = None
) -> tuple[DailyActivityRepository, _ProxyReads]:
    reads: Final = proxy_reads if proxy_reads is not None else _ProxyReads()
    return DailyActivityRepository(_FakePrismaClient(database), proxy_reads=reads), reads


@pytest.mark.asyncio
async def test_aggregated_only_reads_global_marker_for_unfiltered_user_scope() -> None:
    database = _FakeDatabase(((),))
    repository, proxy_reads = _repository(database, _ProxyReads("2026-01-10"))

    await repository.aggregated(_scope(entity_ids=None), include_entity_breakdown=False)

    assert proxy_reads.marker_calls == 1
    assert database.query_calls[0][1][-3:] == (
        PTU_SENTINEL_API_KEY,
        "2026-01-10",
        constants.USAGE_TOP_API_KEYS_LIMIT,
    )
    assert database.query_calls[0][1][-1] == constants.USAGE_TOP_API_KEYS_LIMIT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        _scope(entity_ids=("user-1",)),
        _scope(entity_ids=None, api_keys=("key-1",)),
        _scope(table=DailyActivityTable.TEAM, entity_ids=None),
        _scope(entity_ids=None, api_keys=()),
    ],
)
async def test_aggregated_skips_global_marker_when_scope_is_not_global_user(scope: DailyActivityScope) -> None:
    database = _FakeDatabase(((),))
    repository, proxy_reads = _repository(database)

    await repository.aggregated(scope, include_entity_breakdown=False)

    assert proxy_reads.marker_calls == 0
    assert database.query_calls


@pytest.mark.asyncio
async def test_marker_failure_falls_back_to_per_key_query() -> None:
    database = _FakeDatabase(((),))
    repository, proxy_reads = _repository(database, _ProxyReads(marker_error=True))

    await repository.aggregated(_scope(entity_ids=None), include_entity_breakdown=False)

    assert proxy_reads.marker_calls == 1
    assert database.query_calls[0][1][-2:] == (PTU_SENTINEL_API_KEY, constants.USAGE_TOP_API_KEYS_LIMIT)


@pytest.mark.asyncio
async def test_key_methods_send_builder_query_and_clamp_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(constants, "USAGE_KEY_SEARCH_LIMIT", 2)
    monkeypatch.setattr(constants, "USAGE_MODEL_TOP_KEYS_LIMIT", 2)
    monkeypatch.setattr(constants, "USAGE_CACHE_LEAKAGE_KEYS_LIMIT", 2)
    database = _FakeDatabase(((_key_spend_row("key-a"),), (_key_spend_row("key-b"),), (_key_spend_row("key-c"),)))
    repository, _ = _repository(database)
    scope = _scope()

    assert await repository.search_keys(scope, search="key", limit=10) == ("key-a",)
    model_keys: Final = await repository.model_top_keys(scope, model_group="model-a", by_model_group=True, limit=10)
    leakage_keys: Final = await repository.cache_leakage_keys(scope, limit=10)

    assert tuple(row.api_key for row in model_keys) == ("key-b",)
    assert tuple(row.api_key for row in leakage_keys) == ("key-c",)
    assert model_keys[0].spend == 1.0
    assert leakage_keys[0].prompt_tokens - leakage_keys[0].cache_read_input_tokens == 7
    assert database.query_calls == [
        (
            build_key_search_sql(scope, search="key", limit=2).sql,
            build_key_search_sql(scope, search="key", limit=2).params,
        ),
        (
            build_model_top_keys_sql(scope, model_group="model-a", by_model_group=True, limit=2).sql,
            build_model_top_keys_sql(scope, model_group="model-a", by_model_group=True, limit=2).params,
        ),
        (
            build_cache_leakage_keys_sql(scope, limit=2).sql,
            build_cache_leakage_keys_sql(scope, limit=2).params,
        ),
    ]


@pytest.mark.asyncio
async def test_key_methods_skip_queries_for_nonpositive_limits() -> None:
    database = _FakeDatabase()
    repository, _ = _repository(database)

    assert await repository.search_keys(_scope(), search="key", limit=0) == ()
    assert await repository.model_top_keys(_scope(), model_group="model-a", by_model_group=False, limit=0) == ()
    assert await repository.cache_leakage_keys(_scope(), limit=0) == ()
    assert database.query_calls == []


@pytest.mark.asyncio
async def test_key_spend_validation_rejects_malformed_rows() -> None:
    repository, _ = _repository(_FakeDatabase((({"api_key": "missing-metrics"},),)))

    with pytest.raises(ValidationError):
        await repository.search_keys(_scope(), search="key", limit=1)


@pytest.mark.asyncio
async def test_key_metadata_prefers_active_rows_and_recovers_all_requested_keys() -> None:
    database = _FakeDatabase()
    active: Final = _FakeVerificationToken(
        token="active",
        key_alias="current",
        team_id="team-active",
        user_id="user-active",
        metadata={"tags": ["production", "internal"]},
    )
    deleted_active_duplicate: Final = _FakeDeletedVerificationToken(
        token="active",
        key_alias="stale",
        team_id="team-stale",
        user_id="user-stale",
        metadata={"tags": []},
        deleted_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    deleted_older: Final = _FakeDeletedVerificationToken(
        token="deleted",
        key_alias="older",
        team_id=None,
        user_id=None,
        metadata={"tags": "invalid"},
        deleted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    deleted_newer: Final = _FakeDeletedVerificationToken(
        token="deleted",
        key_alias="newer",
        team_id=None,
        user_id=None,
        metadata={"tags": ["archived"]},
        deleted_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
    )
    database.litellm_verificationtoken = _FakeTable((active,))
    database.litellm_deletedverificationtoken = _FakeTable((deleted_active_duplicate, deleted_older, deleted_newer))
    proxy_reads: Final = _ProxyReads()
    repository, _ = _repository(database, proxy_reads)
    window: Final = (datetime(2026, 1, 1), datetime(2026, 2, 1))
    requested: Final = frozenset(("active", "deleted", "unresolved"))

    result = await repository.key_metadata(requested, window)

    assert result["active"] == KeyMetadataRow(
        api_key="active",
        key_alias="current",
        team_id="team-active",
        user_id="user-active",
        user_email=None,
        key_exists=True,
        tags=("production", "internal"),
    )
    assert result["deleted"].key_alias == "newer"
    assert result["deleted"].key_exists is False
    assert result["deleted"].tags == ("archived",)
    assert len(database.litellm_deletedverificationtoken.find_many_calls) == 1
    assert set(database.litellm_deletedverificationtoken.find_many_calls[0]["token"]["in"]) == {
        "deleted",
        "unresolved",
    }
    assert proxy_reads.recovery_calls == [
        (
            result,
            requested,
            window,
        )
    ]


@pytest.mark.asyncio
async def test_key_metadata_empty_set_does_not_query_tables() -> None:
    database = _FakeDatabase()
    repository, proxy_reads = _repository(database)

    assert await repository.key_metadata(frozenset(), None) == {}
    assert database.litellm_verificationtoken.find_many_calls == []
    assert proxy_reads.recovery_calls == []


@pytest.mark.asyncio
async def test_export_is_lazy_and_uses_the_last_row_as_the_next_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(constants, "USAGE_EXPORT_BATCH_SIZE", 2)
    database = _FakeDatabase(
        (
            (_export_row("key-1"), _export_row("key-2")),
            (_export_row("key-3"), _export_row("key-4")),
            (_export_row("key-5"),),
        )
    )
    repository, _ = _repository(database)
    rows = repository.export_rows(_scope(), export_type=ExportType.DAILY_WITH_KEYS)

    assert database.query_calls == []
    assert (await rows.__anext__()).api_key == "key-1"
    assert len(database.query_calls) == 1
    results = [row async for row in rows]

    assert [row.api_key for row in results] == ["key-2", "key-3", "key-4", "key-5"]
    assert len(database.query_calls) == 3
    assert database.query_calls[1][1][-4:] == ("2026-01-01", "user-1", "key-2", 2)
    assert database.query_calls[2][1][-4:] == ("2026-01-01", "user-1", "key-4", 2)
    assert (
        build_export_sql(
            _scope(),
            export_type=ExportType.DAILY_WITH_KEYS,
            after=ExportCursor("2026-01-01", "user-1", "key-2"),
            batch_size=2,
        ).params
        == database.query_calls[1][1]
    )
