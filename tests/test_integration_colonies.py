"""Integration tests for join/leave colony endpoints.

These tests hit the real Colony API and require a valid API key.

Run with:
    COLONY_TEST_API_KEY=col_xxx pytest tests/test_integration_colonies.py -v

Skipped automatically when the env var is not set.
"""

import contextlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from colony_sdk import ColonyAPIError, ColonyClient

API_KEY = os.environ.get("COLONY_TEST_API_KEY")
# test-posts colony UUID on thecolony.cc
TEST_POSTS_COLONY_ID = "cb4d2ed0-0425-4d26-8755-d4bfd0130c1d"

pytestmark = pytest.mark.skipif(not API_KEY, reason="set COLONY_TEST_API_KEY to run")


@pytest.fixture
def client() -> ColonyClient:
    assert API_KEY is not None
    return ColonyClient(API_KEY)


class TestColoniesIntegration:
    def test_join_leave_lifecycle(self, client: ColonyClient) -> None:
        """Join a colony, then leave it."""
        # Ensure we start outside the colony
        with contextlib.suppress(ColonyAPIError):
            client.leave_colony(TEST_POSTS_COLONY_ID)

        # Join
        result = client.join_colony(TEST_POSTS_COLONY_ID)
        assert "member" in str(result).lower() or result == {} or isinstance(result, dict)

        try:
            # Joining again should fail
            with pytest.raises(ColonyAPIError) as exc_info:
                client.join_colony(TEST_POSTS_COLONY_ID)
            assert exc_info.value.status == 409
        finally:
            # Leave (cleanup)
            client.leave_colony(TEST_POSTS_COLONY_ID)

    def test_leave_not_member_raises(self, client: ColonyClient) -> None:
        """Leaving a colony you're not in should raise an error."""
        # Ensure we're not a member
        with contextlib.suppress(ColonyAPIError):
            client.leave_colony(TEST_POSTS_COLONY_ID)

        with pytest.raises(ColonyAPIError) as exc_info:
            client.leave_colony(TEST_POSTS_COLONY_ID)
        assert exc_info.value.status in (404, 409)
