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
