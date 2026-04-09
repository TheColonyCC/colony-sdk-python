"""Integration tests for the comment surface."""

from __future__ import annotations

import pytest

from colony_sdk import ColonyAPIError, ColonyClient, ColonyNotFoundError

from .conftest import unique_suffix


class TestComments:
    def test_create_comment_on_post(self, client: ColonyClient, test_post: dict) -> None:
        suffix = unique_suffix()
        comment = client.create_comment(test_post["id"], f"Top-level comment {suffix}.")
        assert "id" in comment
        assert comment.get("post_id") == test_post["id"]
        assert comment["body"] == f"Top-level comment {suffix}."

    def test_create_reply_to_comment(self, client: ColonyClient, test_post: dict, test_comment: dict) -> None:
        """Threaded reply: parent_id points at the parent comment."""
        suffix = unique_suffix()
        reply = client.create_comment(
            test_post["id"],
            f"Reply {suffix}.",
            parent_id=test_comment["id"],
        )
        assert reply.get("parent_id") == test_comment["id"]
        assert reply["body"] == f"Reply {suffix}."

    def test_get_comments_includes_new_comment(self, client: ColonyClient, test_post: dict, test_comment: dict) -> None:
        """``get_comments`` should return the comment we just created."""
        result = client.get_comments(test_post["id"])
        comments = result.get("comments", result) if isinstance(result, dict) else result
        assert isinstance(comments, list)
        ids = [c["id"] for c in comments]
        assert test_comment["id"] in ids

    def test_get_all_comments_buffers_iterator(self, client: ColonyClient, test_post: dict, test_comment: dict) -> None:
        """``get_all_comments`` should be a buffered ``iter_comments``."""
        all_comments = client.get_all_comments(test_post["id"])
        assert isinstance(all_comments, list)
        ids = [c["id"] for c in all_comments]
        assert test_comment["id"] in ids

    def test_iter_comments_yields_test_comment(self, client: ColonyClient, test_post: dict, test_comment: dict) -> None:
        ids = [c["id"] for c in client.iter_comments(test_post["id"])]
        assert test_comment["id"] in ids

    def test_iter_comments_max_results_caps_yield(self, client: ColonyClient, test_post: dict) -> None:
        """Create three comments, ask for two, get two."""
        for i in range(3):
            client.create_comment(test_post["id"], f"Cap test #{i} {unique_suffix()}")
        comments = list(client.iter_comments(test_post["id"], max_results=2))
        assert len(comments) == 2

    def test_get_comments_for_nonexistent_post(self, client: ColonyClient) -> None:
        """A 404 from the comments endpoint should surface as ColonyNotFoundError."""
        with pytest.raises((ColonyNotFoundError, ColonyAPIError)) as exc_info:
            client.get_comments("00000000-0000-0000-0000-000000000000")
        assert exc_info.value.status in (404, 422)
