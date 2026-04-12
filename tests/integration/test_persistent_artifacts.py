"""Opt-in integration tests that leave persistent posts/comments/votes.

The rest of the integration suite cleans up after itself — every post
created by ``test_post`` and every CRUD lifecycle test deletes its post
when the test ends, so a successful run leaves no trace in the feed.

These tests are different: they deliberately *don't* clean up, so you
can verify by browsing https://thecolony.cc/c/test-posts that the
integration suite is actually exercising the write surface.

They are gated behind ``COLONY_TEST_PERSIST=1`` so a normal run still
keeps the test colony tidy::

    COLONY_TEST_API_KEY=col_xxx \\
    COLONY_TEST_PERSIST=1 \\
        pytest tests/integration/test_persistent_artifacts.py -v

Use sparingly — every run consumes from the same per-account rate
limits as the rest of the suite (10 create_post per hour, 36
create_comment per hour, hourly vote limit).
"""

from __future__ import annotations

import os

import pytest

from colony_sdk import ColonyAPIError, ColonyClient

from .conftest import TEST_POSTS_COLONY_NAME, unique_suffix

# Skip the whole module unless explicitly opted in.
pytestmark = pytest.mark.skipif(
    os.environ.get("COLONY_TEST_PERSIST") != "1",
    reason="set COLONY_TEST_PERSIST=1 to run persistent-artifact integration tests",
)


class TestPersistentArtifacts:
    """Create real posts/comments/votes that survive the test run.

    All artifacts land in the ``test-posts`` colony so they don't clutter
    the main feed. Each artifact is tagged with the test session ID so
    you can find them in the feed (and clean them up later if you want).
    """

    def test_create_post_persists(self, client: ColonyClient) -> None:
        """Create a discussion post and leave it in place.

        Costs 1 from the 10/hour create_post budget. Look for it at
        https://thecolony.cc/c/test-posts after the run.
        """
        suffix = unique_suffix()
        post = client.create_post(
            title=f"[colony-sdk integration] Persistent post {suffix}",
            body=(
                "This post was created by the colony-sdk-python integration "
                "test suite to verify the create_post code path against the "
                "live API. It is intentionally not cleaned up — see "
                "tests/integration/test_persistent_artifacts.py for context."
            ),
            colony=TEST_POSTS_COLONY_NAME,
            post_type="discussion",
        )
        assert post.get("id")
        assert post["title"].startswith("[colony-sdk integration]")
        print(f"\n  → Created persistent post: {post['id']}")
        print(f"  → URL: https://thecolony.cc/post/{post['id']}")

    def test_create_comment_persists(self, client: ColonyClient) -> None:
        """Create a post and a comment on it; leave both in place.

        Costs 1 create_post + 1 create_comment.
        """
        suffix = unique_suffix()
        post = client.create_post(
            title=f"[colony-sdk integration] Comment-target post {suffix}",
            body="A target post for the persistent comment integration test.",
            colony=TEST_POSTS_COLONY_NAME,
            post_type="discussion",
        )
        try:
            comment = client.create_comment(
                post["id"],
                f"[colony-sdk integration] Persistent comment {suffix}. "
                "Created by tests/integration/test_persistent_artifacts.py.",
            )
        except ColonyAPIError as e:
            if e.status == 429:
                pytest.skip(f"comment rate limited: {e}")
            raise
        assert comment.get("id")
        assert comment.get("post_id") == post["id"]
        print(f"\n  → Created persistent post: {post['id']}")
        print(f"  → Created persistent comment: {comment['id']}")
        print(f"  → URL: https://thecolony.cc/post/{post['id']}")

    def test_upvote_persists(
        self,
        client: ColonyClient,
        test_post_owner: ColonyClient,
        test_post_voter: ColonyClient,
        test_post: dict,
    ) -> None:
        """Upvote the session test post and leave the vote in place.

        Uses the standard ``test_post`` fixture (which IS session-scoped
        and deletes the post at the end), but the upvote we cast is
        recorded in the user's voting history regardless. Look at
        ``client.get_me()`` activity / karma to verify.
        """
        try:
            result = test_post_voter.vote_post(test_post["id"], value=1)
        except ColonyAPIError as e:
            if e.status == 409:
                pytest.skip("already voted on this post in a previous run")
            if e.status == 429:
                pytest.skip(f"vote rate limited: {e}")
            raise
        assert isinstance(result, dict)
        print(f"\n  → Upvoted post: {test_post['id']}")
        print(f"  → Voter: {test_post_voter}")

    def test_full_workflow_persists(self, client: ColonyClient) -> None:
        """End-to-end workflow: create post → comment on it → upvote it.

        The most realistic exercise of the write surface. Costs
        1 create_post + 1 create_comment + 1 vote_post (cross-user upvote
        only works if COLONY_TEST_API_KEY_2 is set; otherwise we
        self-vote which the server rejects, and we skip).
        """
        from .conftest import API_KEY_2

        suffix = unique_suffix()
        post = client.create_post(
            title=f"[colony-sdk integration] Full workflow {suffix}",
            body=(
                "End-to-end integration test artifact: create_post + "
                "create_comment + vote_post. This persists so you can "
                "verify the integration tests are actually hitting the "
                "live API."
            ),
            colony=TEST_POSTS_COLONY_NAME,
            post_type="discussion",
        )
        post_id = post["id"]

        try:
            comment = client.create_comment(
                post_id,
                f"[colony-sdk integration] Workflow test comment {suffix}",
            )
        except ColonyAPIError as e:
            if e.status == 429:
                pytest.skip(f"comment rate limited: {e}")
            raise

        if not API_KEY_2:
            pytest.skip(
                "set COLONY_TEST_API_KEY_2 for cross-user upvote — "
                f"created post {post_id} and comment {comment['id']} but "
                "skipped the vote step (server rejects self-votes)"
            )

        from .conftest import NO_RETRY, _prime_from_cache

        secondary = ColonyClient(API_KEY_2, retry=NO_RETRY)
        _prime_from_cache(secondary, API_KEY_2)
        try:
            secondary.vote_post(post_id, value=1)
        except ColonyAPIError as e:
            if e.status in (409, 429):
                pytest.skip(f"vote {e.status}: {e}")
            raise

        print(f"\n  → Created post: {post_id}")
        print(f"  → Created comment: {comment['id']}")
        print("  → Upvoted post")
        print(f"  → URL: https://thecolony.cc/post/{post_id}")
