"""Integration tests for the post CRUD + listing surface."""

from __future__ import annotations

import contextlib

import pytest

from colony_sdk import ColonyAPIError, ColonyClient, ColonyNotFoundError

from .conftest import TEST_POSTS_COLONY_ID, TEST_POSTS_COLONY_NAME, unique_suffix


class TestPostCRUD:
    def test_create_get_delete_lifecycle(self, client: ColonyClient) -> None:
        """Round-trip a discussion post through create → get → delete."""
        suffix = unique_suffix()
        title = f"CRUD lifecycle {suffix}"
        body = f"Body for CRUD test {suffix}."

        created = client.create_post(
            title=title,
            body=body,
            colony=TEST_POSTS_COLONY_NAME,
            post_type="discussion",
        )
        post_id = created["id"]
        assert created["title"] == title
        assert created["body"] == body
        assert created.get("colony_id") == TEST_POSTS_COLONY_ID

        try:
            fetched = client.get_post(post_id)
            assert fetched["id"] == post_id
            assert fetched["title"] == title
        finally:
            client.delete_post(post_id)

        # Subsequent fetch should 404.
        with pytest.raises(ColonyNotFoundError):
            client.get_post(post_id)

    def test_update_within_edit_window(self, client: ColonyClient) -> None:
        """Posts can be edited within the 15-minute edit window."""
        suffix = unique_suffix()
        post = client.create_post(
            title=f"Update test {suffix}",
            body="Original body.",
            colony=TEST_POSTS_COLONY_NAME,
            post_type="discussion",
        )
        try:
            updated = client.update_post(
                post["id"],
                title=f"Updated title {suffix}",
                body="Updated body.",
            )
            assert updated["title"] == f"Updated title {suffix}"
            assert updated["body"] == "Updated body."

            # Re-fetch and confirm the update is persisted.
            refetched = client.get_post(post["id"])
            assert refetched["title"] == f"Updated title {suffix}"
            assert refetched["body"] == "Updated body."
        finally:
            with contextlib.suppress(ColonyAPIError):
                client.delete_post(post["id"])

    def test_get_nonexistent_post_raises_not_found(self, client: ColonyClient) -> None:
        with pytest.raises(ColonyNotFoundError) as exc_info:
            client.get_post("00000000-0000-0000-0000-000000000000")
        assert exc_info.value.status == 404

    def test_delete_nonexistent_post_raises(self, client: ColonyClient) -> None:
        with pytest.raises(ColonyAPIError) as exc_info:
            client.delete_post("00000000-0000-0000-0000-000000000000")
        assert exc_info.value.status in (403, 404)


class TestPostListing:
    def test_get_posts_returns_list(self, client: ColonyClient) -> None:
        result = client.get_posts(limit=5)
        posts = result.get("posts", result) if isinstance(result, dict) else result
        assert isinstance(posts, list)
        assert len(posts) <= 5
        for post in posts:
            assert "id" in post
            assert "title" in post

    def test_get_posts_filters_by_colony(self, client: ColonyClient, test_post: dict) -> None:
        """Filtering by colony should at least include the just-created post."""
        result = client.get_posts(colony=TEST_POSTS_COLONY_NAME, sort="new", limit=20)
        posts = result.get("posts", result) if isinstance(result, dict) else result
        assert isinstance(posts, list)
        ids = [p["id"] for p in posts]
        assert test_post["id"] in ids

    def test_get_posts_sort_orders_accepted(self, client: ColonyClient) -> None:
        """The four documented sort orders should all return without error."""
        for sort in ("new", "top", "hot", "discussed"):
            result = client.get_posts(sort=sort, limit=3)
            posts = result.get("posts", result) if isinstance(result, dict) else result
            assert isinstance(posts, list), f"sort={sort} returned {type(result)}"

    def test_get_posts_filters_by_post_type(self, client: ColonyClient) -> None:
        """Filtering by post_type only returns matching posts."""
        result = client.get_posts(post_type="discussion", limit=10)
        posts = result.get("posts", result) if isinstance(result, dict) else result
        assert isinstance(posts, list)
        for p in posts:
            # Some posts may not echo post_type — only assert when present.
            if "post_type" in p:
                assert p["post_type"] == "discussion"
