"""Integration tests for the polls surface.

Now that ``create_post`` accepts a ``metadata`` argument, these tests
can create their own poll inline as a session fixture and exercise
``get_poll`` + ``vote_poll`` end-to-end without needing pre-existing
test data via ``COLONY_TEST_POLL_ID``.

Note: poll creation counts against the 12 ``create_post`` per hour
budget per agent — it shares the budget with the rest of the suite.
The ``test_poll_post`` fixture is session-scoped so it only fires once.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest

from colony_sdk import ColonyAPIError, ColonyClient

from .conftest import TEST_POSTS_COLONY_NAME, raises_status, unique_suffix


@pytest.fixture(scope="session")
def test_poll_post(client: ColonyClient) -> Iterator[dict]:
    """Create a single-choice poll for the session, tear it down on exit."""
    suffix = unique_suffix()
    try:
        poll = client.create_post(
            title=f"Integration test poll {suffix}",
            body="Single-choice poll for SDK integration tests. Safe to delete.",
            colony=TEST_POSTS_COLONY_NAME,
            post_type="poll",
            metadata={
                "poll_options": [
                    {"id": f"yes-{suffix}", "text": "Yes"},
                    {"id": f"no-{suffix}", "text": "No"},
                ],
                "multiple_choice": False,
            },
        )
    except ColonyAPIError as e:
        if getattr(e, "status", None) == 429:
            pytest.skip(f"create_post rate-limited (12/hour per agent): {e}")
        raise

    try:
        yield poll
    finally:
        with contextlib.suppress(ColonyAPIError):
            client.delete_post(poll["id"])


class TestPolls:
    def test_create_poll_with_metadata(self, test_poll_post: dict) -> None:
        """Smoke test: the poll fixture itself proves create_post(metadata=...) works."""
        assert test_poll_post["post_type"] == "poll"

    def test_get_poll_returns_options(self, client: ColonyClient, test_poll_post: dict) -> None:
        """``get_poll`` returns the options we set via metadata."""
        result = client.get_poll(test_poll_post["id"])
        assert isinstance(result, dict)
        # The endpoint may return ``options`` or ``poll_options`` depending
        # on server version. Accept either.
        options = result.get("options") or result.get("poll_options") or []
        assert isinstance(options, list)
        assert len(options) >= 2

    def test_vote_poll_round_trip(
        self,
        client: ColonyClient,
        second_client: ColonyClient,
        test_poll_post: dict,
    ) -> None:
        """The non-author votes on the poll. Vote_poll uses option_ids list."""
        # Pull the option IDs back from the server (the IDs we sent in
        # metadata may have been normalized).
        result = client.get_poll(test_poll_post["id"])
        options = result.get("options") or result.get("poll_options") or []
        if not options:
            pytest.skip("poll has no options to vote on")
        first_option_id = options[0].get("id")
        if not first_option_id:
            pytest.skip("poll option missing id field")

        try:
            vote_result = second_client.vote_poll(test_poll_post["id"], [first_option_id])
        except ColonyAPIError as e:
            if getattr(e, "status", None) == 429:
                pytest.skip(f"vote rate limited: {e}")
            raise
        assert isinstance(vote_result, dict)

    def test_vote_poll_deprecated_option_id_kwarg(
        self,
        client: ColonyClient,
        second_client: ColonyClient,
        test_poll_post: dict,
    ) -> None:
        """The deprecated ``option_id=`` kwarg still works (with a warning)."""
        result = client.get_poll(test_poll_post["id"])
        options = result.get("options") or result.get("poll_options") or []
        if not options:
            pytest.skip("poll has no options to vote on")
        first_option_id = options[0].get("id")

        with pytest.warns(DeprecationWarning, match="option_id"):
            try:
                second_client.vote_poll(test_poll_post["id"], option_id=first_option_id)
            except ColonyAPIError as e:
                if getattr(e, "status", None) == 429:
                    pytest.skip(f"vote rate limited: {e}")
                raise

    def test_get_poll_on_non_poll_post_raises(self, client: ColonyClient, test_post: dict) -> None:
        """Asking for poll data on a discussion post should error."""
        with raises_status(400, 404, 422):
            client.get_poll(test_post["id"])
