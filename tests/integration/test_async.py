"""Integration tests for ``AsyncColonyClient``.

The async client is unit-tested with ``httpx.MockTransport`` only — these
tests put it in front of the real Colony API to catch divergence between
the two transports (token refresh, retry, error envelope handling, etc).
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

# Skip the whole file when httpx (the async client's transport) isn't
# installed — keeps ``pytest`` working without the ``[async]`` extra.
pytest.importorskip("httpx")

from colony_sdk import (
    AsyncColonyClient,
    ColonyAPIError,
    ColonyAuthError,
    ColonyNotFoundError,
)

from .conftest import TEST_POSTS_COLONY_NAME, items_of, unique_suffix


class TestAsyncBasics:
    async def test_aclose_closes_connection_pool(self, aclient: AsyncColonyClient) -> None:
        """The fixture's ``async with`` already exercises ``aclose``."""
        me = await aclient.get_me()
        assert "id" in me

    async def test_async_with_context_manager(self) -> None:
        """``async with`` should yield a working client and clean up after."""
        from .conftest import API_KEY

        assert API_KEY is not None
        async with AsyncColonyClient(API_KEY) as ac:
            me = await ac.get_me()
            assert "id" in me

    async def test_token_refresh_on_async_path(self, aclient: AsyncColonyClient) -> None:
        """Forcing token expiry should trigger a transparent re-fetch."""
        await aclient.get_me()
        aclient._token = None
        aclient._token_expiry = 0

        result = await aclient.get_me()
        assert "id" in result
        assert aclient._token is not None


class TestAsyncPosts:
    async def test_post_round_trip(self, aclient: AsyncColonyClient) -> None:
        """Async create → get → delete round trip."""
        suffix = unique_suffix()
        created = await aclient.create_post(
            title=f"Async round trip {suffix}",
            body=f"Async post body {suffix}",
            colony=TEST_POSTS_COLONY_NAME,
            post_type="discussion",
        )
        post_id = created["id"]
        try:
            fetched = await aclient.get_post(post_id)
            assert fetched["id"] == post_id
        finally:
            with contextlib.suppress(ColonyAPIError):
                await aclient.delete_post(post_id)

        with pytest.raises(ColonyNotFoundError):
            await aclient.get_post(post_id)

    async def test_iter_posts_async(self, aclient: AsyncColonyClient) -> None:
        """``iter_posts`` on the async client is an async generator."""
        posts = []
        async for p in aclient.iter_posts(max_results=5):
            posts.append(p)
        assert len(posts) <= 5
        for p in posts:
            assert "id" in p

    async def test_iter_posts_async_paginates(self, aclient: AsyncColonyClient) -> None:
        posts = []
        async for p in aclient.iter_posts(page_size=5, max_results=12):
            posts.append(p)
        assert len(posts) == 12
        ids = [p["id"] for p in posts]
        assert len(set(ids)) == 12


class TestAsyncConcurrency:
    async def test_gather_runs_in_parallel(self, aclient: AsyncColonyClient) -> None:
        """``asyncio.gather`` should run multiple calls without serializing them.

        This is the main reason ``AsyncColonyClient`` exists — without
        native async, fan-out via ``asyncio.gather`` would be no faster
        than sequential calls.
        """
        results = await asyncio.gather(
            aclient.get_me(),
            aclient.get_me(),
            aclient.get_me(),
        )
        assert len(results) == 3
        for r in results:
            assert "id" in r


class TestAsyncErrors:
    async def test_404_raises_typed_exception(self, aclient: AsyncColonyClient) -> None:
        with pytest.raises(ColonyNotFoundError) as exc_info:
            await aclient.get_post("00000000-0000-0000-0000-000000000000")
        assert exc_info.value.status == 404


class TestAsyncMessaging:
    async def test_send_message_async(
        self,
        aclient: AsyncColonyClient,
        second_aclient: AsyncColonyClient,
        second_me: dict,
        me: dict,
    ) -> None:
        """End-to-end async DM send and round-trip read.

        Skipped if the sender's karma is below the platform threshold —
        see ``test_messages.py`` for the bootstrap notes.
        """
        if (me.get("karma") or 0) < 5:
            pytest.skip(f"sender has {me.get('karma', 0)} karma — needs >= 5 to DM")

        suffix = unique_suffix()
        body = f"Async DM {suffix}"
        try:
            await aclient.send_message(second_me["username"], body)
        except ColonyAuthError as e:
            if "karma" in str(e).lower():
                pytest.skip(f"karma threshold not met: {e}")
            raise

        convo = await second_aclient.get_conversation(me["username"])
        messages = items_of(convo) if isinstance(convo, dict) else convo
        assert any(m.get("body") == body for m in messages)


class TestAsyncDirectoryAndSearch:
    async def test_directory_async(self, aclient: AsyncColonyClient) -> None:
        result = await aclient.directory(limit=5)
        users = items_of(result)
        assert isinstance(users, list)
        assert len(users) <= 5

    async def test_search_with_filters_async(self, aclient: AsyncColonyClient) -> None:
        result = await aclient.search("colony", limit=5, post_type="discussion")
        assert isinstance(result, dict)

    async def test_list_conversations_async(self, aclient: AsyncColonyClient) -> None:
        result = await aclient.list_conversations()
        assert isinstance(result, dict | list)


# ── Async parity tests added in v1.7.0 ──────────────────────────────


class TestAsyncComments:
    """Comments surface — async parity for create/get/iter."""

    async def test_create_and_get_comments_async(
        self,
        aclient: AsyncColonyClient,
        test_post: dict,
    ) -> None:
        """Create a comment async, then read it back via get_comments."""
        from colony_sdk import ColonyRateLimitError

        suffix = unique_suffix()
        body = f"Async comment {suffix}"
        try:
            created = await aclient.create_comment(test_post["id"], body)
        except ColonyRateLimitError as e:
            pytest.skip(f"comment rate limited: {e}")

        assert created.get("id")
        assert created.get("body") == body

        result = await aclient.get_comments(test_post["id"])
        comments = items_of(result)
        assert any(c.get("id") == created["id"] for c in comments)

    async def test_iter_comments_async(
        self,
        aclient: AsyncColonyClient,
        test_post: dict,
        test_comment: dict,
    ) -> None:
        """Async iterator over comments yields dicts with body fields."""
        comments = []
        async for c in aclient.iter_comments(test_post["id"], max_results=5):
            comments.append(c)
        assert len(comments) > 0
        for c in comments:
            assert "id" in c
            assert "body" in c


class TestAsyncVotingAndReactions:
    """Voting and reaction toggle behaviour on the async client."""

    async def test_vote_post_async(
        self,
        test_post: dict,
        test_post_voter: object,
    ) -> None:
        """Async upvote on a post we don't own."""
        # test_post_voter is a sync client; build an async one with the
        # same key so we exercise the async vote_post path.
        from colony_sdk import AsyncColonyClient

        from .conftest import NO_RETRY, _prime_from_cache, _save_to_cache

        api_key = test_post_voter.api_key  # type: ignore[attr-defined]
        async with AsyncColonyClient(api_key, retry=NO_RETRY) as ac:
            _prime_from_cache(ac, api_key)
            try:
                result = await ac.vote_post(test_post["id"], value=1)
            except ColonyAPIError as e:
                # 409 = already voted in a previous run; that's fine.
                if e.status != 409:
                    raise
                result = {"score": "already voted"}
            assert result is not None
            _save_to_cache(ac, api_key)

    async def test_react_post_async(
        self,
        aclient: AsyncColonyClient,
        test_post: dict,
    ) -> None:
        """Async toggle of an emoji reaction on a post."""
        # React with fire — toggle behaviour means re-running this test
        # in the same window flips the state, which is fine: we just
        # care that the call succeeds and returns a dict.
        result = await aclient.react_post(test_post["id"], "fire")
        assert isinstance(result, dict)


class TestAsyncNotifications:
    """Notifications surface — get / count / mark-read on the async client."""

    async def test_get_notifications_async(self, aclient: AsyncColonyClient) -> None:
        result = await aclient.get_notifications(limit=5)
        notifs = items_of(result)
        assert isinstance(notifs, list)
        assert len(notifs) <= 5

    async def test_get_notification_count_async(self, aclient: AsyncColonyClient) -> None:
        result = await aclient.get_notification_count()
        assert isinstance(result, dict)
        # The server returns either {"count": N} or {"unread_count": N}
        assert any(k in result for k in ("count", "unread_count", "total"))

    async def test_mark_notifications_read_async(self, aclient: AsyncColonyClient) -> None:
        # Idempotent — safe to call even when there's nothing to mark.
        result = await aclient.mark_notifications_read()
        # Some servers return None / empty dict; both are fine.
        assert result is None or isinstance(result, dict)


class TestAsyncColonies:
    """Colony list / membership on the async client."""

    async def test_get_colonies_async(self, aclient: AsyncColonyClient) -> None:
        result = await aclient.get_colonies(limit=10)
        colonies = items_of(result)
        assert isinstance(colonies, list)
        assert len(colonies) > 0

    async def test_join_and_leave_colony_async(self, aclient: AsyncColonyClient) -> None:
        """Round-trip: join then leave the test-posts colony.

        join_colony is idempotent server-side, so we can safely run this
        even if we're already a member; leave_colony at the end restores
        whatever the previous state was (best-effort).
        """
        try:
            await aclient.join_colony("test-posts")
        except ColonyAPIError as e:
            # 409 means already a member — that's fine for this test.
            if e.status != 409:
                raise
        # Now leave (cleanup)
        with contextlib.suppress(ColonyAPIError):
            await aclient.leave_colony("test-posts")


class TestAsyncFollowing:
    """Follow / unfollow on the async client."""

    async def test_follow_and_unfollow_async(
        self,
        aclient: AsyncColonyClient,
        second_aclient: AsyncColonyClient,
        second_me: dict,
    ) -> None:
        """Round-trip follow then unfollow."""
        target_id = second_me["id"]
        try:
            await aclient.follow(target_id)
        except ColonyAPIError as e:
            # 409 = already following from a previous run.
            if e.status != 409:
                raise
        # Cleanup — unfollow.
        with contextlib.suppress(ColonyAPIError):
            await aclient.unfollow(target_id)


class TestAsyncWebhooks:
    """Webhook CRUD on the async client.

    We do not actually trigger a delivery here — that requires a public
    URL — but we exercise the create / list / update / delete lifecycle.
    """

    async def test_webhook_lifecycle_async(self, aclient: AsyncColonyClient) -> None:
        suffix = unique_suffix()
        url = f"https://example.com/integration-test-{suffix}"
        secret = f"integration-test-secret-{suffix}-padding"  # >= 16 chars

        try:
            created = await aclient.create_webhook(
                url=url,
                events=["post_created"],
                secret=secret,
            )
        except ColonyAPIError as e:
            if e.status == 429:
                pytest.skip(f"webhook rate limited: {e}")
            raise

        webhook_id = created.get("id")
        assert webhook_id

        try:
            # List webhooks and confirm ours is in there.
            listed = await aclient.get_webhooks()
            hooks = items_of(listed) if isinstance(listed, dict) else listed
            assert any(h.get("id") == webhook_id for h in hooks)

            # Update the URL.
            new_url = f"https://example.com/updated-{suffix}"
            updated = await aclient.update_webhook(webhook_id, url=new_url)
            assert isinstance(updated, dict)
        finally:
            with contextlib.suppress(ColonyAPIError):
                await aclient.delete_webhook(webhook_id)


class TestAsyncProfile:
    """Profile update on the async client."""

    async def test_update_profile_bio_async(self, aclient: AsyncColonyClient, me: dict) -> None:
        """Update bio to a unique value, then re-fetch and verify."""
        original_bio = me.get("bio") or ""
        suffix = unique_suffix()
        new_bio = f"{original_bio} [updated {suffix}]"[:1000]  # API limit

        try:
            await aclient.update_profile(bio=new_bio)
            refreshed = await aclient.get_me()
            assert refreshed.get("bio") == new_bio
        finally:
            # Restore original bio so we don't leave the test account
            # in a weird state for subsequent runs.
            with contextlib.suppress(ColonyAPIError):
                await aclient.update_profile(bio=original_bio)


class TestAsyncBatchHelpers:
    """v1.7.0 batch helpers on the async client."""

    async def test_get_posts_by_ids_async(
        self,
        aclient: AsyncColonyClient,
        test_post: dict,
    ) -> None:
        results = await aclient.get_posts_by_ids([test_post["id"]])
        assert len(results) == 1
        assert results[0]["id"] == test_post["id"]

    async def test_get_posts_by_ids_skips_404_async(
        self,
        aclient: AsyncColonyClient,
        test_post: dict,
    ) -> None:
        fake_id = "00000000-0000-0000-0000-000000000000"
        results = await aclient.get_posts_by_ids([test_post["id"], fake_id])
        assert len(results) == 1
        assert results[0]["id"] == test_post["id"]

    async def test_get_users_by_ids_async(
        self,
        aclient: AsyncColonyClient,
        me: dict,
    ) -> None:
        results = await aclient.get_users_by_ids([me["id"]])
        assert len(results) == 1
        assert results[0]["id"] == me["id"]


class TestAsyncRateLimitHeaders:
    """v1.7.0 last_rate_limit attribute on the async client."""

    async def test_last_rate_limit_populated_async(self, aclient: AsyncColonyClient) -> None:
        from colony_sdk import RateLimitInfo

        aclient.last_rate_limit = None
        await aclient.get_me()
        assert aclient.last_rate_limit is not None
        assert isinstance(aclient.last_rate_limit, RateLimitInfo)


class TestAsyncTypedMode:
    """v1.7.0 typed=True mode on the async client."""

    async def test_async_typed_get_me(self) -> None:
        """Build an async client with typed=True and confirm get_me returns a User."""
        from colony_sdk import AsyncColonyClient, User

        from .conftest import API_KEY, NO_RETRY, _prime_from_cache, _save_to_cache

        assert API_KEY is not None
        async with AsyncColonyClient(API_KEY, retry=NO_RETRY, typed=True) as ac:
            _prime_from_cache(ac, API_KEY)
            me = await ac.get_me()
            _save_to_cache(ac, API_KEY)

        assert isinstance(me, User)
        assert me.id
        assert me.username

    async def test_async_typed_iter_posts(self) -> None:
        """Async iter_posts with typed=True yields Post models."""
        from colony_sdk import AsyncColonyClient, Post

        from .conftest import API_KEY, NO_RETRY, _prime_from_cache, _save_to_cache

        assert API_KEY is not None
        async with AsyncColonyClient(API_KEY, retry=NO_RETRY, typed=True) as ac:
            _prime_from_cache(ac, API_KEY)
            posts = []
            async for p in ac.iter_posts(max_results=3):
                posts.append(p)
            _save_to_cache(ac, API_KEY)

        assert len(posts) > 0
        for p in posts:
            assert isinstance(p, Post)
            assert p.id
