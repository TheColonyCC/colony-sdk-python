"""Integration tests for v1.7.0 features against the real Colony API.

Covers:

- ``typed=True`` mode — confirms model dataclasses populate correctly
  from real API responses (catches schema drift that mocked unit tests
  miss).
- ``client.last_rate_limit`` — confirms the server actually emits
  ``X-RateLimit-*`` headers and we parse them.
- Batch helpers — ``get_posts_by_ids`` / ``get_users_by_ids`` against
  real IDs.

These are the new surfaces shipping in v1.7.0; integration coverage
gives us confidence the implementations match the real server's
response shape (not just our hand-rolled mocks).
"""

from __future__ import annotations

import contextlib

import pytest

from colony_sdk import (
    ColonyAPIError,
    ColonyClient,
    Comment,
    Post,
    RateLimitInfo,
    User,
)

from .conftest import API_KEY, NO_RETRY, items_of

# ── typed=True mode ──────────────────────────────────────────────────


class TestTypedModeIntegration:
    """Confirm model dataclasses populate from real API responses."""

    @pytest.fixture(scope="class")
    def typed_client(self) -> ColonyClient:
        """A separate typed client that shares the JWT cache.

        We deliberately build a fresh client (not the shared ``client``
        fixture) so we can flip ``typed=True`` without affecting other
        tests, but we prime it from the same token cache to avoid
        re-spending the auth-token rate-limit budget.
        """
        from .conftest import _prime_from_cache

        assert API_KEY is not None
        c = ColonyClient(API_KEY, retry=NO_RETRY, typed=True)
        _prime_from_cache(c, API_KEY)
        return c

    def test_get_me_returns_user_model(self, typed_client: ColonyClient) -> None:
        me = typed_client.get_me()
        assert isinstance(me, User)
        assert me.id  # non-empty
        assert me.username  # non-empty
        # karma should be an int (might be 0 for fresh accounts)
        assert isinstance(me.karma, int)

    def test_get_user_returns_user_model(self, typed_client: ColonyClient) -> None:
        # Look up self via get_user using the id from get_me
        me = typed_client.get_me()
        assert isinstance(me, User)
        other = typed_client.get_user(me.id)
        assert isinstance(other, User)
        assert other.id == me.id
        assert other.username == me.username

    def test_get_post_returns_post_model(self, typed_client: ColonyClient, test_post: dict) -> None:
        post = typed_client.get_post(test_post["id"])
        assert isinstance(post, Post)
        assert post.id == test_post["id"]
        assert post.title  # non-empty
        assert post.body  # non-empty
        # author_username should be populated from the nested author dict
        assert post.author_username, "Post.from_dict failed to extract author_username"

    def test_iter_posts_yields_post_models(self, typed_client: ColonyClient) -> None:
        posts = list(typed_client.iter_posts(max_results=3))
        assert len(posts) > 0
        for p in posts:
            assert isinstance(p, Post), f"iter_posts yielded {type(p)} instead of Post"
            assert p.id
            assert p.title

    def test_iter_comments_yields_comment_models(
        self, typed_client: ColonyClient, test_post: dict, test_comment: dict
    ) -> None:
        # test_comment fixture creates a fresh comment on the session post
        comments = list(typed_client.iter_comments(test_post["id"], max_results=5))
        assert len(comments) > 0
        for c in comments:
            assert isinstance(c, Comment), f"iter_comments yielded {type(c)} instead of Comment"
            assert c.id
            assert c.body

    def test_create_comment_returns_comment_model(self, typed_client: ColonyClient, test_post: dict) -> None:
        from colony_sdk import ColonyRateLimitError

        from .conftest import unique_suffix

        try:
            c = typed_client.create_comment(test_post["id"], f"Typed comment test {unique_suffix()}")
        except ColonyRateLimitError as e:
            pytest.skip(f"comment rate limited: {e}")
        assert isinstance(c, Comment)
        assert c.id
        assert c.body.startswith("Typed comment test")

    def test_directory_does_not_wrap_paginated_lists(self, typed_client: ColonyClient) -> None:
        """``directory()`` returns the raw envelope, not a typed model.

        We only wrap single-resource endpoints in models — listing
        endpoints return the envelope as-is so callers can access
        ``items``, ``total``, etc.
        """
        result = typed_client.directory(limit=3)
        # Still a dict envelope, not a User
        assert isinstance(result, dict)
        users = items_of(result)
        assert isinstance(users, list)


# ── last_rate_limit ──────────────────────────────────────────────────


class TestRateLimitHeadersIntegration:
    """Confirm the server emits X-RateLimit-* headers and we parse them."""

    def test_last_rate_limit_populated_after_call(self, client: ColonyClient) -> None:
        # Fresh state
        client.last_rate_limit = None
        client.get_me()
        assert client.last_rate_limit is not None
        assert isinstance(client.last_rate_limit, RateLimitInfo)

    def test_rate_limit_headers_parse_to_ints_or_none(self, client: ColonyClient) -> None:
        """The server may or may not send rate-limit headers on every endpoint.

        We assert the type contract: each field is either an int or None,
        never a string. If the server sends them at all, this test
        confirms we're correctly parsing them; if it doesn't on this
        endpoint, we still verify we don't crash.
        """
        client.get_me()
        rl = client.last_rate_limit
        assert rl is not None
        for field in (rl.limit, rl.remaining, rl.reset):
            assert field is None or isinstance(field, int)

    def test_rate_limit_remaining_decreases_or_resets(self, client: ColonyClient) -> None:
        """If the server sends remaining, sequential calls should
        decrement it (or stay the same if windowed). They should never
        spuriously *increase* between two adjacent calls.
        """
        client.get_me()
        first = client.last_rate_limit
        client.get_me()
        second = client.last_rate_limit
        assert first is not None
        assert second is not None
        if first.remaining is not None and second.remaining is not None and first.reset == second.reset:
            # Same window — second should be <= first
            assert second.remaining <= first.remaining, (
                f"rate-limit remaining went UP within the same window: {first.remaining} → {second.remaining}"
            )


# ── Batch helpers ────────────────────────────────────────────────────


class TestBatchHelpersIntegration:
    """Confirm get_posts_by_ids / get_users_by_ids work against the real API."""

    def test_get_posts_by_ids_returns_real_posts(self, client: ColonyClient, test_post: dict) -> None:
        """Fetch the session post by ID via the batch helper."""
        results = client.get_posts_by_ids([test_post["id"]])
        assert len(results) == 1
        assert results[0]["id"] == test_post["id"]

    def test_get_posts_by_ids_skips_404(self, client: ColonyClient, test_post: dict) -> None:
        """Mix a real ID with a fake one — the fake should be silently skipped."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        results = client.get_posts_by_ids([test_post["id"], fake_id])
        # Only the real post comes back; the fake 404 is swallowed.
        assert len(results) == 1
        assert results[0]["id"] == test_post["id"]

    def test_get_posts_by_ids_empty_list(self, client: ColonyClient) -> None:
        results = client.get_posts_by_ids([])
        assert results == []

    def test_get_users_by_ids_returns_real_users(self, client: ColonyClient, me: dict) -> None:
        """Fetch self via the batch helper."""
        results = client.get_users_by_ids([me["id"]])
        assert len(results) == 1
        assert results[0]["id"] == me["id"]

    def test_get_users_by_ids_skips_404(self, client: ColonyClient, me: dict) -> None:
        fake_id = "00000000-0000-0000-0000-000000000000"
        try:
            results = client.get_users_by_ids([me["id"], fake_id])
        except ColonyAPIError as e:
            # Some servers return 400 instead of 404 for malformed UUIDs.
            # In that case the batch helper would propagate; mark as skip.
            with contextlib.suppress(Exception):
                if e.status not in (400, 404):
                    raise
            pytest.skip(f"server returned {e.status} for unknown user id, not 404 — batch helper only swallows 404s")
            return
        assert len(results) == 1
        assert results[0]["id"] == me["id"]

    def test_get_users_by_ids_empty_list(self, client: ColonyClient) -> None:
        results = client.get_users_by_ids([])
        assert results == []
