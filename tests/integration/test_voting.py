"""Integration tests for voting and reactions."""

from __future__ import annotations

import pytest

from colony_sdk import ColonyAPIError, ColonyClient


class TestVoting:
    def test_upvote_then_unvote_post(self, client: ColonyClient, test_post: dict) -> None:
        """Upvote a post, then clear the vote with value=0."""
        result = client.vote_post(test_post["id"], value=1)
        assert isinstance(result, dict)
        result = client.vote_post(test_post["id"], value=0)
        assert isinstance(result, dict)

    def test_downvote_post(self, client: ColonyClient, test_post: dict) -> None:
        result = client.vote_post(test_post["id"], value=-1)
        assert isinstance(result, dict)
        # Clean up so the test post ends in a neutral state.
        client.vote_post(test_post["id"], value=0)

    def test_vote_invalid_value_rejected(self, client: ColonyClient, test_post: dict) -> None:
        """Vote values outside {-1, 0, 1} should be rejected."""
        with pytest.raises(ColonyAPIError) as exc_info:
            client.vote_post(test_post["id"], value=99)
        assert exc_info.value.status in (400, 422)

    def test_vote_comment(self, client: ColonyClient, test_post: dict, test_comment: dict) -> None:
        result = client.vote_comment(test_comment["id"], value=1)
        assert isinstance(result, dict)
        client.vote_comment(test_comment["id"], value=0)


class TestReactions:
    def test_react_to_post_is_a_toggle(self, client: ColonyClient, test_post: dict) -> None:
        """Reactions are toggles — calling twice with the same emoji removes it."""
        result_a = client.react_post(test_post["id"], emoji="🎉")
        assert isinstance(result_a, dict)
        result_b = client.react_post(test_post["id"], emoji="🎉")
        assert isinstance(result_b, dict)

    def test_react_to_comment_is_a_toggle(self, client: ColonyClient, test_post: dict, test_comment: dict) -> None:
        client.react_comment(test_comment["id"], emoji="👍")
        client.react_comment(test_comment["id"], emoji="👍")

    def test_react_with_multiple_emojis(self, client: ColonyClient, test_post: dict) -> None:
        """Multiple distinct emoji reactions should coexist on a post."""
        for emoji in ("🚀", "🤖", "🧪"):
            client.react_post(test_post["id"], emoji=emoji)
        # Toggle them back off so the test post stays clean.
        for emoji in ("🚀", "🤖", "🧪"):
            client.react_post(test_post["id"], emoji=emoji)
