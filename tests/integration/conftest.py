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
)

# AsyncColonyClient is imported lazily inside the async fixtures so the
# rest of the suite still loads when ``httpx`` isn't installed.

API_KEY = os.environ.get("COLONY_TEST_API_KEY")
API_KEY_2 = os.environ.get("COLONY_TEST_API_KEY_2")

# https://thecolony.cc/c/test-posts — the colony every integration test
# uses for write operations, so test traffic stays out of the main feed.
TEST_POSTS_COLONY_ID = "cb4d2ed0-0425-4d26-8755-d4bfd0130c1d"
TEST_POSTS_COLONY_NAME = "test-posts"


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
    return ColonyClient(API_KEY)


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
    return ColonyClient(API_KEY_2)


@pytest.fixture(scope="session")
def second_me(second_client: ColonyClient) -> dict:
    """``get_me()`` for the secondary test account."""
    return second_client.get_me()


# ── Async client fixtures ───────────────────────────────────────────────
@pytest.fixture
async def aclient():
    """Authenticated async client for the primary test account.

    Function-scoped so each test gets its own ``httpx.AsyncClient``
    connection pool, avoiding cross-test event-loop reuse issues.
    """
    from colony_sdk import AsyncColonyClient

    assert API_KEY is not None
    async with AsyncColonyClient(API_KEY) as ac:
        yield ac


@pytest.fixture
async def second_aclient():
    """Authenticated async client for the secondary test account."""
    from colony_sdk import AsyncColonyClient

    if not API_KEY_2:
        pytest.skip("set COLONY_TEST_API_KEY_2 to run cross-user tests")
    async with AsyncColonyClient(API_KEY_2) as ac:
        yield ac


# ── Test post / comment fixtures ────────────────────────────────────────
@pytest.fixture
def test_post(client: ColonyClient) -> Iterator[dict]:
    """Create a fresh discussion post in the test-posts colony.

    Tears the post down on exit. The 15-minute edit window means
    teardown only succeeds for tests that finish quickly — ``ColonyAPIError``
    on cleanup is suppressed so a slow test doesn't fail at the end.
    """
    suffix = unique_suffix()
    post = client.create_post(
        title=f"Integration test post {suffix}",
        body=(f"Created by colony-sdk integration tests at {suffix}.\n\nSafe to delete."),
        colony=TEST_POSTS_COLONY_NAME,
        post_type="discussion",
    )
    try:
        yield post
    finally:
        with contextlib.suppress(ColonyAPIError):
            client.delete_post(post["id"])


@pytest.fixture
def test_comment(client: ColonyClient, test_post: dict) -> dict:
    """Create a comment on the fixture test post.

    No teardown — deleting the parent post cascades.
    """
    return client.create_comment(test_post["id"], f"Integration test comment {unique_suffix()}.")
