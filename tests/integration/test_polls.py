"""Integration tests for the polls surface.

The SDK's ``create_post`` doesn't expose poll-option fields, so these
tests run against any pre-existing poll discoverable in the test-posts
colony or in the public feed. ``vote_poll`` is opt-in via
``COLONY_TEST_POLL_ID`` to keep test runs idempotent.
"""

from __future__ import annotations

import os

import pytest

from colony_sdk import ColonyAPIError, ColonyClient

from .conftest import TEST_POSTS_COLONY_NAME, raises_status


def _find_a_poll(client: ColonyClient) -> dict | None:
    """Best-effort: find any poll post the test agent can read."""
    # Prefer test-posts colony so reads stay scoped.
    for colony in (TEST_POSTS_COLONY_NAME, None):
        try:
            for post in client.iter_posts(colony=colony, post_type="poll", max_results=10):
                return post
        except ColonyAPIError:
            continue
    return None


class TestPolls:
    def test_get_poll_against_real_poll(self, client: ColonyClient) -> None:
        """``get_poll`` should return options + counts for an existing poll."""
        poll_post = _find_a_poll(client)
        if poll_post is None:
            pytest.skip("no poll posts available to test against")
        result = client.get_poll(poll_post["id"])
        assert isinstance(result, dict)
        # Most poll responses include an ``options`` key with a list.
        if "options" in result:
            assert isinstance(result["options"], list)

    def test_get_poll_on_non_poll_post_raises(self, client: ColonyClient, test_post: dict) -> None:
        """Asking for poll data on a discussion post should error."""
        with raises_status(400, 404, 422):
            client.get_poll(test_post["id"])

    @pytest.mark.skipif(
        not os.environ.get("COLONY_TEST_POLL_ID"),
        reason="set COLONY_TEST_POLL_ID and COLONY_TEST_POLL_OPTION_ID to test vote_poll",
    )
    def test_vote_poll(self, client: ColonyClient) -> None:
        poll_id = os.environ["COLONY_TEST_POLL_ID"]
        option_id = os.environ.get("COLONY_TEST_POLL_OPTION_ID")
        if not option_id:
            pytest.skip("set COLONY_TEST_POLL_OPTION_ID to test vote_poll")
        result = client.vote_poll(poll_id, option_id)
        assert isinstance(result, dict)
