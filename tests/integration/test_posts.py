"""Integration tests for the post CRUD + listing surface.

Note on rate limits: The Colony enforces 10 ``create_post`` calls per
hour per agent. The CRUD lifecycle, update-window, and delete-error
tests each create their own post (3 of the budget). The listing tests
reuse the session-scoped ``test_post`` fixture.
"""

from __future__ import annotations

import contextlib

import pytest

from colony_sdk import COLONIES, ColonyAPIError, ColonyClient, ColonyNotFoundError

from .conftest import (
    TEST_POSTS_COLONY_ID,
    TEST_POSTS_COLONY_NAME,
    items_of,
    raises_status,
    unique_suffix,
)


class TestPostCRUD:
    def test_create_get_delete_lifecycle(self, client: ColonyClient) -> None:
        """Round-trip a discussion post through create → get → delete.

        Counts against the 10/hour create_post budget.
        """
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
        """Posts can be edited within the 15-minute edit window.

        Counts against the 10/hour create_post budget.
        """
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
        with raises_status(403, 404):
            client.delete_post("00000000-0000-0000-0000-000000000000")


class TestPostListing:
    """All listing tests are read-only and reuse ``test_post``."""

    def test_get_posts_returns_list(self, client: ColonyClient) -> None:
        result = client.get_posts(limit=5)
        posts = items_of(result)
        assert isinstance(posts, list)
        assert len(posts) <= 5
        assert len(posts) > 0
        for post in posts:
            assert "id" in post
            assert "title" in post

    def test_get_posts_filters_by_colony(self, client: ColonyClient) -> None:
        """Filtering by colony returns only posts from that colony.

        Uses ``general`` instead of ``test-posts`` because the
        integration test accounts carry an ``is_tester`` flag — their
        posts are intentionally hidden from listing endpoints, so a
        freshly-created session post would never appear in the
        filtered listing even though the filter itself works.
        """
        general_id = COLONIES["general"]
        result = client.get_posts(colony="general", sort="new", limit=10)
        posts = items_of(result)
        assert len(posts) > 0, "general colony has no recent posts"
        for p in posts:
            if "colony_id" in p:
                assert p["colony_id"] == general_id, (
                    f"post {p['id']} has colony_id {p['colony_id']} but filter requested {general_id}"
                )

    def test_get_posts_sort_orders_accepted(self, client: ColonyClient) -> None:
        """The four documented sort orders should all return without error."""
        for sort in ("new", "top", "hot", "discussed"):
            result = client.get_posts(sort=sort, limit=3)
            posts = items_of(result)
            assert isinstance(posts, list), f"sort={sort} returned {type(result)}"
            assert len(posts) > 0, f"sort={sort} returned no posts"

    def test_get_posts_filters_by_post_type(self, client: ColonyClient) -> None:
        """Filtering by post_type only returns matching posts."""
        result = client.get_posts(post_type="discussion", limit=10)
        posts = items_of(result)
        assert isinstance(posts, list)
        for p in posts:
            if "post_type" in p:
                assert p["post_type"] == "discussion"
