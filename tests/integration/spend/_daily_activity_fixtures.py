from typing import Final

import psycopg
from psycopg import sql

_TABLE_NAMES: Final = (
    "LiteLLM_DailyUserSpend",
    "LiteLLM_DailyTeamSpend",
    "LiteLLM_VerificationToken",
    "LiteLLM_DeletedVerificationToken",
    "LiteLLM_UserTable",
    "LiteLLM_TeamTable",
)


def seed_daily_activity_fixture(connection: psycopg.Connection, *, schema: str, ptu_sentinel_api_key: str) -> None:
    daily_user_table: Final = sql.Identifier(schema, "LiteLLM_DailyUserSpend")
    daily_team_table: Final = sql.Identifier(schema, "LiteLLM_DailyTeamSpend")
    verification_token_table: Final = sql.Identifier(schema, "LiteLLM_VerificationToken")
    deleted_token_table: Final = sql.Identifier(schema, "LiteLLM_DeletedVerificationToken")
    user_table: Final = sql.Identifier(schema, "LiteLLM_UserTable")
    team_table: Final = sql.Identifier(schema, "LiteLLM_TeamTable")
    keys: Final = (
        ("key-a", "model-popular", 100.0, 2, 1),
        ("key-b", "model-popular", 90.0, 3, 1),
        ("key-c", "model-popular", 80.0, 4, 1),
        ("key-target", "model-target", 1.0, 5, 2),
        ("key-cache", "model-cache", 2.0, 1000, 1),
    )
    user_rows: Final = tuple(
        (
            f"user-row-{index}",
            "user-1",
            "2026-06-01",
            api_key,
            model,
            "",
            "provider-a",
            None,
            "/v1/chat/completions",
            prompt_tokens,
            2,
            cache_read_tokens,
            0,
            spend,
            1,
            1,
            0,
            "2026-06-01 12:00:00",
        )
        for index, (api_key, model, spend, prompt_tokens, cache_read_tokens) in enumerate(keys)
    )
    team_rows: Final = tuple(
        (
            f"team-row-{index}",
            "team-1",
            "2026-06-01",
            api_key,
            model,
            "",
            "provider-a",
            None,
            "/v1/chat/completions",
            prompt_tokens,
            2,
            cache_read_tokens,
            0,
            spend,
            1,
            1,
            0,
            0.0,
            "2026-06-01 12:00:00",
        )
        for index, (api_key, model, spend, prompt_tokens, cache_read_tokens) in enumerate(keys)
    )
    sentinel_user_row: Final = (
        "user-row-ptu",
        "user-1",
        "2026-06-01",
        ptu_sentinel_api_key,
        "model-ptu",
        "",
        "provider-a",
        None,
        "/v1/chat/completions",
        0,
        0,
        0,
        0,
        1000.0,
        0,
        0,
        0,
        "2026-06-01 12:00:00",
    )
    sentinel_team_row: Final = (
        "team-row-ptu",
        "team-1",
        "2026-06-01",
        ptu_sentinel_api_key,
        "model-ptu",
        "",
        "provider-a",
        None,
        "/v1/chat/completions",
        0,
        0,
        0,
        0,
        1000.0,
        0,
        0,
        0,
        42.0,
        "2026-06-01 12:00:00",
    )
    with connection.cursor() as cursor:
        for table_name in _TABLE_NAMES:
            cursor.execute(
                sql.SQL("CREATE TABLE {} (LIKE {} INCLUDING DEFAULTS INCLUDING CONSTRAINTS)").format(
                    sql.Identifier(schema, table_name),
                    sql.Identifier(table_name),
                )
            )
        cursor.executemany(
            sql.SQL("""
            INSERT INTO {}
                (id, user_id, date, api_key, model, model_group, custom_llm_provider,
                 mcp_namespaced_tool_name, endpoint, prompt_tokens, completion_tokens,
                 cache_read_input_tokens, cache_creation_input_tokens, spend, api_requests,
                 successful_requests, failed_requests, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """).format(daily_user_table),
            (*user_rows, sentinel_user_row),
        )
        cursor.executemany(
            sql.SQL("""
            INSERT INTO {}
                (id, team_id, date, api_key, model, model_group, custom_llm_provider,
                 mcp_namespaced_tool_name, endpoint, prompt_tokens, completion_tokens,
                 cache_read_input_tokens, cache_creation_input_tokens, spend, api_requests,
                 successful_requests, failed_requests, ptu_flat_cost, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """).format(daily_team_table),
            (*team_rows, sentinel_team_row),
        )
        cursor.executemany(
            sql.SQL(
                "INSERT INTO {} (token, key_alias, team_id, user_id, metadata, models) VALUES (%s, %s, %s, %s, %s, %s)"
            ).format(verification_token_table),
            (
                ("key-a", "alias-a", "team-1", "user-1", '{"tags": ["blue", "gold"]}', []),
                ("key-b", "alias-b", "team-1", "user-1", '{"tags": []}', []),
                ("key-c", "alias-c", "team-1", "user-1", '{"tags": []}', []),
                ("key-cache", "alias-cache", "team-1", "user-1", '{"tags": []}', []),
            ),
        )
        cursor.executemany(
            sql.SQL("""
            INSERT INTO {}
                (id, token, key_alias, team_id, user_id, metadata, models, deleted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """).format(deleted_token_table),
            (
                ("deleted-old", "key-target", "older-target", "team-1", "user-1", '{"tags": []}', [], "2026-06-01"),
                (
                    "deleted-new",
                    "key-target",
                    "deleted-target",
                    "team-1",
                    "user-1",
                    '{"tags": ["archived"]}',
                    [],
                    "2026-06-02",
                ),
            ),
        )
        cursor.execute(
            sql.SQL("INSERT INTO {} (user_id, user_email, models) VALUES (%s, %s, %s)").format(user_table),
            ("user-1", "user@example.com", []),
        )
        cursor.execute(
            sql.SQL("INSERT INTO {} (team_id, team_alias, admins, members, models) VALUES (%s, %s, %s, %s, %s)").format(
                team_table
            ),
            ("team-1", "Usage Team", [], [], []),
        )
    connection.commit()
