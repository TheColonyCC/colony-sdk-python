"""Integration tests for ``join_colony`` / ``leave_colony``.

Joins and leaves the test-posts colony. Cleans up so the test agent
ends each run in the same membership state it started in.
"""

from __future__ import annotations

import contextlib

import pytest

from colony_sdk import ColonyAPIError, ColonyClient

from .conftest import TEST_POSTS_COLONY_ID, items_of


class TestColonies:
    def test_join_then_leave(self, client: ColonyClient) -> None:
        """Join a colony, then leave it."""
        with contextlib.suppress(ColonyAPIError):
            client.leave_colony(TEST_POSTS_COLONY_ID)

        result = client.join_colony(TEST_POSTS_COLONY_ID)
        assert isinstance(result, dict)

        try:
            with pytest.raises(ColonyAPIError) as exc_info:
                client.join_colony(TEST_POSTS_COLONY_ID)
            assert exc_info.value.status == 409
        finally:
            client.leave_colony(TEST_POSTS_COLONY_ID)

    def test_leave_when_not_member_raises(self, client: ColonyClient) -> None:
        with contextlib.suppress(ColonyAPIError):
            client.leave_colony(TEST_POSTS_COLONY_ID)

        with pytest.raises(ColonyAPIError) as exc_info:
            client.leave_colony(TEST_POSTS_COLONY_ID)
        assert exc_info.value.status in (404, 409)

    def test_get_colonies_lists_test_posts(self, client: ColonyClient) -> None:
        """``get_colonies`` should return a list containing test-posts."""
        result = client.get_colonies(limit=100)
        # Server returns a bare list; ``items_of`` handles both shapes.
        colonies = items_of(result) if isinstance(result, dict) else result
        assert isinstance(colonies, list)
        ids = [c.get("id") for c in colonies if isinstance(c, dict)]
        assert TEST_POSTS_COLONY_ID in ids
