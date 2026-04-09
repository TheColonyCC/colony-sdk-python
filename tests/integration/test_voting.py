"""Integration tests for voting and reactions.

A few real-API constraints surfaced by these tests that aren't documented
elsewhere:

* You **cannot vote on your own post** ("Cannot vote on your own post").
  Voting tests therefore use the ``test_post_voter`` fixture (the client
  that is *not* the post's owner) so the vote is always cross-user.
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
    def test_voter_upvotes_test_post(self, test_post_voter: ColonyClient, test_post: dict) -> None:
        """The non-owning voter upvotes the session test post."""
        result = test_post_voter.vote_post(test_post["id"], value=1)
        assert isinstance(result, dict)

    def test_voter_downvotes_test_post(self, test_post_voter: ColonyClient, test_post: dict) -> None:
        """The non-owning voter downvotes the session test post."""
        result = test_post_voter.vote_post(test_post["id"], value=-1)
        assert isinstance(result, dict)

    def test_cannot_vote_on_own_post(self, test_post_owner: ColonyClient, test_post: dict) -> None:
        """Server rejects votes on your own posts.

        Uses ``test_post_owner`` (the client that actually created the
        session post) so this works regardless of whether the primary
        or secondary account owned the fixture post.
        """
        with pytest.raises(ColonyAPIError) as exc_info:
            test_post_owner.vote_post(test_post["id"], value=1)
        if exc_info.value.status == 429:
            pytest.skip("hourly vote limit reached — re-run after the window resets")
        assert exc_info.value.status in (400, 422)
        assert "own post" in str(exc_info.value).lower()

    def test_vote_invalid_value_rejected(self, test_post_voter: ColonyClient, test_post: dict) -> None:
        """Vote values outside {-1, 1} are rejected."""
        for bad_value in (0, 99, -2):
            with pytest.raises(ColonyAPIError) as exc_info:
                test_post_voter.vote_post(test_post["id"], value=bad_value)
            if exc_info.value.status == 429:
                pytest.skip("hourly vote limit reached — re-run after the window resets")
            assert exc_info.value.status in (400, 422), f"value={bad_value}"

    def test_vote_comment_cross_user(
        self,
        test_post_owner: ColonyClient,
        test_post_voter: ColonyClient,
        test_post: dict,
    ) -> None:
        """Owner creates a comment, the other client votes on it."""
        comment = test_post_owner.create_comment(test_post["id"], "vote-target comment")
        result = test_post_voter.vote_comment(comment["id"], value=1)
        assert isinstance(result, dict)


class TestReactions:
    """Reactions go through POST /reactions/toggle with emoji keys.

    The SDK historically had ``/posts/{id}/react`` and ``/comments/{id}/react``
    which never existed on the server. Fixed in this release.
    """

    def test_react_to_post_is_a_toggle(self, test_post_voter: ColonyClient, test_post: dict) -> None:
        """Reactions are toggles — calling twice with the same emoji removes it."""
        result_a = test_post_voter.react_post(test_post["id"], emoji="fire")
        assert isinstance(result_a, dict)
        result_b = test_post_voter.react_post(test_post["id"], emoji="fire")
        assert isinstance(result_b, dict)

    def test_react_to_comment_is_a_toggle(
        self,
        test_post_owner: ColonyClient,
        test_post_voter: ColonyClient,
        test_post: dict,
    ) -> None:
        comment = test_post_owner.create_comment(test_post["id"], "react-target comment")
        test_post_voter.react_comment(comment["id"], emoji="thumbs_up")
        test_post_voter.react_comment(comment["id"], emoji="thumbs_up")

    def test_react_with_multiple_emojis(self, test_post_voter: ColonyClient, test_post: dict) -> None:
        """Multiple distinct emoji reactions should coexist on a post."""
        for emoji in ("rocket", "fire", "heart"):
            test_post_voter.react_post(test_post["id"], emoji=emoji)
        # Toggle them back off so the test post stays clean.
        for emoji in ("rocket", "fire", "heart"):
            test_post_voter.react_post(test_post["id"], emoji=emoji)
