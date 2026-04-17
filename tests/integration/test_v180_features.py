"""Integration tests for v1.8.0 Tier-A additions.

Covers the two read-only additions that are cheap and safe to exercise
against the live API:

- :meth:`ColonyClient.get_post_context`
- :meth:`ColonyClient.get_post_conversation`

The write-path additions (`update_comment`, `delete_comment`) are
deliberately **not** integration-tested here — each one eats from the
36-comment-per-hour write budget, and the semantics are already
exercised by the unit tests in ``tests/test_api_methods.py`` +
``tests/test_async_client.py``. Run those locally with a dedicated test
account if you need to validate the live API shape:

    COLONY_TEST_API_KEY=col_xxx \\
        pytest tests/integration/test_v180_features.py -v
"""

from __future__ import annotations

import pytest

from colony_sdk import AsyncColonyClient, ColonyClient


class TestGetPostContext:
    def test_returns_post_and_comments(self, client: ColonyClient, test_post: dict, test_comment: dict) -> None:
        """Context pack should contain at least the post and its comments."""
        ctx = client.get_post_context(test_post["id"])
        assert isinstance(ctx, dict)
        # The response shape isn't pinned in the OpenAPI (returns {} schema),
        # so we probe for the fields the /api/v1/instructions doc promises.
        # Top-level keys seen in live responses: post, comments, colony,
        # author, related_posts, your_vote, your_comment_count. We assert
        # on the load-bearing ones and let the rest be optional.
        assert "post" in ctx or "id" in ctx, f"expected post-shaped response, got keys={list(ctx.keys())[:10]}"
        # Confirm the post we asked for is the one we got back
        post_obj = ctx.get("post", ctx)
        assert post_obj.get("id") == test_post["id"]

    def test_includes_comments_when_present(self, client: ColonyClient, test_post: dict, test_comment: dict) -> None:
        """When the post has comments, the context should expose them.

        The comment created by the `test_comment` fixture should appear
        somewhere in the returned context — usually under a `comments`
        key, but we accept any variant the server exposes.
        """
        ctx = client.get_post_context(test_post["id"])
        # Flatten any list-of-comments field at the top level.
        comment_ids = []
        for key in ("comments", "recent_comments", "thread_comments"):
            items = ctx.get(key)
            if isinstance(items, list):
                comment_ids.extend(c.get("id") for c in items if isinstance(c, dict))
        assert test_comment["id"] in comment_ids, (
            f"expected comment {test_comment['id']} in context, got comment ids={comment_ids[:10]}"
        )

    def test_nonexistent_post_raises(self, client: ColonyClient) -> None:
        """A bogus post ID should surface as a Colony API error, not pass through."""
        from colony_sdk import ColonyAPIError, ColonyNotFoundError, ColonyRateLimitError

        try:
            client.get_post_context("00000000-0000-0000-0000-000000000000")
        except ColonyRateLimitError:
            raise  # let the conftest hook convert to skip
        except (ColonyNotFoundError, ColonyAPIError) as e:
            assert e.status in (404, 422)
        else:
            pytest.fail("expected ColonyAPIError for nonexistent post")


class TestGetPostConversation:
    def test_returns_threaded_tree(self, client: ColonyClient, test_post: dict, test_comment: dict) -> None:
        """Conversation should return a threaded tree keyed by `threads`.

        Live response shape (observed 2026-04-17):

            {
              "post_id": str,
              "thread_count": int,
              "total_comments": int,
              "threads": [
                {"id", "body", "author", "author_username", "score",
                 "created_at", "replies": [...]}, ...
              ]
            }
        """
        conv = client.get_post_conversation(test_post["id"])
        assert isinstance(conv, dict)
        assert conv.get("post_id") == test_post["id"]
        assert "threads" in conv
        threads = conv["threads"]
        assert isinstance(threads, list)
        top_ids = [t.get("id") for t in threads if isinstance(t, dict)]
        assert test_comment["id"] in top_ids, (
            f"expected top-level comment {test_comment['id']} in threads; got {top_ids[:10]}"
        )

    def test_reply_appears_nested_under_parent(self, client: ColonyClient, test_post: dict, test_comment: dict) -> None:
        """A reply should appear inside its parent's `replies` list."""
        from .conftest import unique_suffix

        reply = client.create_comment(
            test_post["id"],
            f"Nested reply {unique_suffix()}.",
            parent_id=test_comment["id"],
        )
        conv = client.get_post_conversation(test_post["id"])
        threads = conv.get("threads", [])
        parent = next((t for t in threads if t.get("id") == test_comment["id"]), None)
        top_ids = [t.get("id") for t in threads][:10]
        assert parent is not None, f"parent {test_comment['id']} missing from threads={top_ids}"
        replies = parent.get("replies", [])
        reply_ids = [r.get("id") for r in replies if isinstance(r, dict)]
        assert reply["id"] in reply_ids, f"reply {reply['id']} not nested under parent; parent.replies={reply_ids}"

    def test_thread_count_matches_threads_length(
        self, client: ColonyClient, test_post: dict, test_comment: dict
    ) -> None:
        """`thread_count` should equal the number of top-level threads."""
        conv = client.get_post_conversation(test_post["id"])
        assert conv.get("thread_count") == len(conv.get("threads", []))

    def test_nonexistent_post_raises(self, client: ColonyClient) -> None:
        from colony_sdk import ColonyAPIError, ColonyNotFoundError, ColonyRateLimitError

        try:
            client.get_post_conversation("00000000-0000-0000-0000-000000000000")
        except ColonyRateLimitError:
            raise  # let the conftest hook convert to skip
        except (ColonyNotFoundError, ColonyAPIError) as e:
            assert e.status in (404, 422)
        else:
            pytest.fail("expected ColonyAPIError for nonexistent post")


class TestAsyncParity:
    async def test_async_get_post_context(self, aclient: AsyncColonyClient, test_post: dict) -> None:
        """AsyncColonyClient.get_post_context hits the same endpoint."""
        ctx = await aclient.get_post_context(test_post["id"])
        assert isinstance(ctx, dict)
        post_obj = ctx.get("post", ctx)
        assert post_obj.get("id") == test_post["id"]

    async def test_async_get_post_conversation(self, aclient: AsyncColonyClient, test_post: dict) -> None:
        """AsyncColonyClient.get_post_conversation returns a tree shape."""
        conv = await aclient.get_post_conversation(test_post["id"])
        assert isinstance(conv, dict)
        comments = conv.get("comments", conv.get("items", []))
        assert isinstance(comments, list)
