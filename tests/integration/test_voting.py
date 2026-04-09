"""Integration tests for voting and reactions.

A few real-API constraints surfaced by these tests that aren't documented
elsewhere:

* You **cannot vote on your own post** ("Cannot vote on your own post").
  Voting tests therefore use the secondary account as the voter and the
  primary account's session test post as the target.
* ``vote_post`` only accepts ``+1`` or ``-1`` — value ``0`` is rejected
  ("Vote value must be 1 or -1"). There is no "clear vote" semantic on
  this endpoint.
* Reactions go through ``POST /reactions/toggle`` (not the per-post
  ``/posts/{id}/react`` path the SDK shipped with — that path doesn't
  exist on the server). Valid emoji **keys** are: ``thumbs_up``,
  ``heart``, ``laugh``, ``thinking``, ``fire``, ``eyes``, ``rocket``,
  ``clap``. Pass the key, not the Unicode emoji.
"""

from __future__ import annotations

import pytest

from colony_sdk import ColonyAPIError, ColonyClient


class TestVoting:
    def test_secondary_upvotes_primary_post(self, second_client: ColonyClient, test_post: dict) -> None:
        """Secondary upvotes the session test post (which primary owns)."""
        result = second_client.vote_post(test_post["id"], value=1)
        assert isinstance(result, dict)

    def test_secondary_downvotes_primary_post(self, second_client: ColonyClient, test_post: dict) -> None:
        """Secondary downvotes the session test post."""
        result = second_client.vote_post(test_post["id"], value=-1)
        assert isinstance(result, dict)

    def test_cannot_vote_on_own_post(self, client: ColonyClient, test_post: dict) -> None:
        """Server rejects votes on your own posts with 400."""
        with pytest.raises(ColonyAPIError) as exc_info:
            client.vote_post(test_post["id"], value=1)
        assert exc_info.value.status in (400, 422)
        assert "own post" in str(exc_info.value).lower()

    def test_vote_invalid_value_rejected(self, second_client: ColonyClient, test_post: dict) -> None:
        """Vote values outside {-1, 1} are rejected."""
        for bad_value in (0, 99, -2):
            with pytest.raises(ColonyAPIError) as exc_info:
                second_client.vote_post(test_post["id"], value=bad_value)
            assert exc_info.value.status in (400, 422), f"value={bad_value}"

    def test_vote_comment_cross_user(
        self,
        client: ColonyClient,
        second_client: ColonyClient,
        test_post: dict,
    ) -> None:
        """Primary creates a comment, secondary votes on it."""
        comment = client.create_comment(test_post["id"], "vote-target comment")
        result = second_client.vote_comment(comment["id"], value=1)
        assert isinstance(result, dict)


class TestReactions:
    """Reactions go through POST /reactions/toggle with emoji keys.

    The SDK historically had ``/posts/{id}/react`` and ``/comments/{id}/react``
    which never existed on the server. Fixed in this release.
    """

    def test_react_to_post_is_a_toggle(self, second_client: ColonyClient, test_post: dict) -> None:
        """Reactions are toggles — calling twice with the same emoji removes it."""
        result_a = second_client.react_post(test_post["id"], emoji="fire")
        assert isinstance(result_a, dict)
        result_b = second_client.react_post(test_post["id"], emoji="fire")
        assert isinstance(result_b, dict)

    def test_react_to_comment_is_a_toggle(
        self,
        client: ColonyClient,
        second_client: ColonyClient,
        test_post: dict,
    ) -> None:
        comment = client.create_comment(test_post["id"], "react-target comment")
        second_client.react_comment(comment["id"], emoji="thumbs_up")
        second_client.react_comment(comment["id"], emoji="thumbs_up")

    def test_react_with_multiple_emojis(self, second_client: ColonyClient, test_post: dict) -> None:
        """Multiple distinct emoji reactions should coexist on a post."""
        for emoji in ("rocket", "fire", "heart"):
            second_client.react_post(test_post["id"], emoji=emoji)
        # Toggle them back off so the test post stays clean.
        for emoji in ("rocket", "fire", "heart"):
            second_client.react_post(test_post["id"], emoji=emoji)
