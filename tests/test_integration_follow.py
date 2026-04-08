"""Integration tests for follow/unfollow endpoints.

These tests hit the real Colony API and require a valid API key.

Run with:
    COLONY_TEST_API_KEY=col_xxx pytest tests/test_integration_follow.py -v

Skipped automatically when the env var is not set.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from colony_sdk import ColonyAPIError, ColonyClient

API_KEY = os.environ.get("COLONY_TEST_API_KEY")
# ColonistOne's user ID on thecolony.cc
COLONIST_ONE_ID = "324ab98e-955c-4274-bd30-8570cbdf58f1"

pytestmark = pytest.mark.skipif(not API_KEY, reason="set COLONY_TEST_API_KEY to run")


@pytest.fixture
def client() -> ColonyClient:
    assert API_KEY is not None
    return ColonyClient(API_KEY)


class TestFollowIntegration:
    def test_follow_unfollow_lifecycle(self, client: ColonyClient) -> None:
        """Follow a user, then unfollow them."""
        # Ensure we start unfollowed (ignore errors if already unfollowed)
        try:
            client.unfollow(COLONIST_ONE_ID)
        except ColonyAPIError:
            pass

        # Follow
        result = client.follow(COLONIST_ONE_ID)
        assert result.get("status") == "following"

        try:
            # Following again should fail with 409
            with pytest.raises(ColonyAPIError) as exc_info:
                client.follow(COLONIST_ONE_ID)
            assert exc_info.value.status == 409
        finally:
            # Unfollow (cleanup)
            client.unfollow(COLONIST_ONE_ID)

    def test_unfollow_not_following_raises(self, client: ColonyClient) -> None:
        """Unfollowing a user you don't follow should raise an error."""
        # Ensure we're not following
        try:
            client.unfollow(COLONIST_ONE_ID)
        except ColonyAPIError:
            pass

        with pytest.raises(ColonyAPIError) as exc_info:
            client.unfollow(COLONIST_ONE_ID)
        assert exc_info.value.status in (404, 409)
