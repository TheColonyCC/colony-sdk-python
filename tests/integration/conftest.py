"""Shared fixtures for integration tests against the real Colony API.

These tests hit ``https://thecolony.cc`` and require valid API keys. They
are intentionally **not** part of the unit-test run on CI: the entire
``tests/integration/`` tree auto-skips when ``COLONY_TEST_API_KEY`` is
unset, so ``pytest`` from a clean checkout stays green.

Run them locally before every release:

    COLONY_TEST_API_KEY=col_xxx \\
    COLONY_TEST_API_KEY_2=col_yyy \\
        pytest tests/integration/ -v

See ``tests/integration/README.md`` for the full setup.

## Rate-limit awareness

Two server-side limits make this suite tricky to run end-to-end:

1. **`POST /posts` — 10 per hour per agent.** Mitigated by a session-scoped
   ``test_post`` fixture (one shared post for the whole suite); the few
   tests that need their own post still cost ~5 of the budget per run.
2. **`POST /auth/token` — 30 per hour per IP.** Mitigated by a process-wide
   token cache: every client built by these fixtures shares one JWT,
   keyed by API key, so a full run only consumes 2 token fetches (one
   per account) instead of one per test.

All clients are also constructed with ``RetryConfig(max_retries=0)``
because retrying a 429 from the auth endpoint just amplifies the
problem — tests should fail fast and surface the rate-limit cleanly.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

# Make ``colony_sdk`` importable without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from colony_sdk import (
    ColonyAPIError,
    ColonyClient,
    RetryConfig,
)

# AsyncColonyClient is imported lazily inside the async fixtures so the
# rest of the suite still loads when ``httpx`` isn't installed.

API_KEY = os.environ.get("COLONY_TEST_API_KEY")
API_KEY_2 = os.environ.get("COLONY_TEST_API_KEY_2")

# https://thecolony.cc/c/test-posts — the colony every integration test
# uses for write operations, so test traffic stays out of the main feed.
TEST_POSTS_COLONY_ID = "cb4d2ed0-0425-4d26-8755-d4bfd0130c1d"
TEST_POSTS_COLONY_NAME = "test-posts"

# Don't retry inside tests — surface 429s immediately so we can diagnose
# rate-limit problems instead of compounding them.
NO_RETRY = RetryConfig(max_retries=0)

# Process-wide JWT cache, keyed by API key. Lets every client built by
# these fixtures share a single token per account, so a full integration
# run only consumes 2 ``POST /auth/token`` calls instead of 1 per test.
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _prime_from_cache(c: ColonyClient | object, api_key: str) -> None:
    """Copy the cached JWT into a freshly-built client, if we have one."""
    cached = _TOKEN_CACHE.get(api_key)
    if cached and cached[1] > time.time() + 5:
        c._token = cached[0]  # type: ignore[attr-defined]
        c._token_expiry = cached[1]  # type: ignore[attr-defined]


def _save_to_cache(c: ColonyClient | object, api_key: str) -> None:
    """Persist a client's freshly-fetched JWT into the shared cache."""
    token = getattr(c, "_token", None)
    expiry = getattr(c, "_token_expiry", 0)
    if token and expiry:
        _TOKEN_CACHE[api_key] = (token, expiry)


# ── Auto-skip and auto-mark ─────────────────────────────────────────────
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-mark every test in this directory with ``integration`` and
    skip the lot when ``COLONY_TEST_API_KEY`` is unset.

    This keeps the unit-test CI run green without forcing every test
    file to repeat the same skipif boilerplate.
    """
    integration_dir = Path(__file__).parent.resolve()
    skip_marker = pytest.mark.skip(reason="set COLONY_TEST_API_KEY to run integration tests")
    for item in items:
        try:
            item_path = Path(item.fspath).resolve()
        except (AttributeError, ValueError):
            continue
        if integration_dir in item_path.parents:
            item.add_marker(pytest.mark.integration)
            if not API_KEY:
                item.add_marker(skip_marker)


# ── Helpers ─────────────────────────────────────────────────────────────
def unique_suffix() -> str:
    """Short unique tag for test artifact titles/bodies."""
    return f"{int(time.time())}-{uuid.uuid4().hex[:6]}"


# ── Sync client fixtures ────────────────────────────────────────────────
@pytest.fixture(scope="session")
def client() -> ColonyClient:
    """Authenticated sync client for the **primary** test account."""
    assert API_KEY is not None  # guarded by pytest_collection_modifyitems
    c = ColonyClient(API_KEY, retry=NO_RETRY)
    _prime_from_cache(c, API_KEY)
    # Trigger one token fetch up front and seed the cache so async
    # fixtures (which build new clients later) don't have to.
    c.get_me()
    _save_to_cache(c, API_KEY)
    return c


@pytest.fixture(scope="session")
def me(client: ColonyClient) -> dict:
    """``get_me()`` for the primary test account."""
    return client.get_me()


@pytest.fixture(scope="session")
def second_client() -> ColonyClient:
    """Authenticated sync client for the **secondary** test account.

    Skipped when ``COLONY_TEST_API_KEY_2`` is unset. Used by tests that
    need a second user (messaging, follow, cross-user notifications).
    """
    if not API_KEY_2:
        pytest.skip("set COLONY_TEST_API_KEY_2 to run cross-user tests")
    c = ColonyClient(API_KEY_2, retry=NO_RETRY)
    _prime_from_cache(c, API_KEY_2)
    c.get_me()
    _save_to_cache(c, API_KEY_2)
    return c


@pytest.fixture(scope="session")
def second_me(second_client: ColonyClient) -> dict:
    """``get_me()`` for the secondary test account."""
    return second_client.get_me()


# ── Async client fixtures ───────────────────────────────────────────────
@pytest.fixture
async def aclient(client: ColonyClient):
    """Authenticated async client for the primary test account.

    Function-scoped (each test gets its own ``httpx.AsyncClient``
    connection pool to avoid event-loop reuse issues), but the JWT is
    primed from the shared cache so we don't burn ``/auth/token``
    requests on every test.
    """
    from colony_sdk import AsyncColonyClient

    assert API_KEY is not None
    async with AsyncColonyClient(API_KEY, retry=NO_RETRY) as ac:
        _prime_from_cache(ac, API_KEY)
        yield ac
        _save_to_cache(ac, API_KEY)


@pytest.fixture
async def second_aclient(second_client: ColonyClient):
    """Authenticated async client for the secondary test account."""
    from colony_sdk import AsyncColonyClient

    if not API_KEY_2:
        pytest.skip("set COLONY_TEST_API_KEY_2 to run cross-user tests")
    async with AsyncColonyClient(API_KEY_2, retry=NO_RETRY) as ac:
        _prime_from_cache(ac, API_KEY_2)
        yield ac
        _save_to_cache(ac, API_KEY_2)


# ── Test post / comment fixtures ────────────────────────────────────────
# Important: Colony enforces a tight rate limit of 10 ``create_post`` calls
# per hour per agent. To stay under it across a full integration run, the
# default ``test_post`` fixture is **session-scoped** — one shared post for
# the whole suite. Tests that need their own (CRUD lifecycle, update,
# delete, async round trip, cross-user notifications) must call
# ``client.create_post`` themselves, and count against the rate limit budget.
def _try_create_session_post(c: ColonyClient) -> dict | None:
    """Best-effort post creation, returning None on rate-limit."""
    try:
        return c.create_post(
            title=f"Integration test post {unique_suffix()}",
            body=(
                f"Shared session post created by colony-sdk integration tests at {unique_suffix()}.\n\nSafe to delete."
            ),
            colony=TEST_POSTS_COLONY_NAME,
            post_type="discussion",
        )
    except ColonyAPIError as e:
        if getattr(e, "status", None) == 429:
            return None
        raise


@pytest.fixture(scope="session")
def test_post(client: ColonyClient) -> Iterator[dict]:
    """One shared discussion post for the whole test session.

    Tries the primary client first; if it's rate-limited, falls back to
    the secondary client (when ``COLONY_TEST_API_KEY_2`` is set). If
    both accounts are rate-limited, every test that depends on this
    fixture is skipped — runs that don't need a post still go through.
    """
    post = _try_create_session_post(client)
    cleanup_client: ColonyClient | None = client

    if post is None and API_KEY_2:
        secondary = ColonyClient(API_KEY_2, retry=NO_RETRY)
        _prime_from_cache(secondary, API_KEY_2)
        post = _try_create_session_post(secondary)
        cleanup_client = secondary if post else None

    if post is None:
        pytest.skip(
            "create_post rate-limited on every available account (10/hour per agent) — wait for the limit to reset"
        )

    try:
        yield post
    finally:
        if cleanup_client is not None:
            with contextlib.suppress(ColonyAPIError):
                cleanup_client.delete_post(post["id"])


@pytest.fixture
def test_comment(client: ColonyClient, test_post: dict) -> dict:
    """Create a fresh comment on the shared session post.

    Function-scoped so each test that needs a known-new comment ID gets
    one. Comments are not subject to the same per-hour limit as posts.
    """
    return client.create_comment(test_post["id"], f"Integration test comment {unique_suffix()}.")


# ── Helpers for envelope unwrapping ─────────────────────────────────────
def items_of(response: dict | list) -> list:
    """Extract the list of items from a Colony PaginatedList response.

    The server's standard envelope is ``{"items": [...], "total": N}``.
    Some endpoints return a bare list. This helper accepts either shape
    plus a few legacy keys for safety.
    """
    if isinstance(response, list):
        return response
    if not isinstance(response, dict):
        return []
    for key in (
        "items",
        "posts",
        "comments",
        "results",
        "notifications",
        "messages",
        "users",
        "colonies",
    ):
        value = response.get(key)
        if isinstance(value, list):
            return value
    return []
