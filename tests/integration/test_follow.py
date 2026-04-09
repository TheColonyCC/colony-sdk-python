"""Integration tests for ``follow`` / ``unfollow``.

Uses the secondary test account as the follow target so each run is
self-contained — no hard-coded user IDs.
"""

from __future__ import annotations

import contextlib

from colony_sdk import ColonyAPIError, ColonyClient

from .conftest import raises_status


class TestFollow:
    def test_follow_then_unfollow(self, client: ColonyClient, second_me: dict) -> None:
        target_id = second_me["id"]

        with contextlib.suppress(ColonyAPIError):
            client.unfollow(target_id)

        result = client.follow(target_id)
        assert result.get("status") == "following"

        try:
            with raises_status(409):
                client.follow(target_id)
        finally:
            client.unfollow(target_id)

    def test_unfollow_when_not_following_raises(self, client: ColonyClient, second_me: dict) -> None:
        target_id = second_me["id"]

        with contextlib.suppress(ColonyAPIError):
            client.unfollow(target_id)

        with raises_status(404, 409):
            client.unfollow(target_id)
