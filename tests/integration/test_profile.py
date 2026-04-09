"""Integration tests for profile, user lookup, search, and directory."""

from __future__ import annotations

import pytest

from colony_sdk import ColonyClient

from .conftest import items_of, raises_status, unique_suffix


class TestProfile:
    def test_get_me(self, client: ColonyClient) -> None:
        me = client.get_me()
        assert "id" in me
        assert "username" in me

    def test_get_user_by_id(self, client: ColonyClient, me: dict) -> None:
        """Looking up your own ID via ``get_user`` returns the same record."""
        result = client.get_user(me["id"])
        assert result["id"] == me["id"]
        assert result["username"] == me["username"]

    def test_get_nonexistent_user_raises(self, client: ColonyClient) -> None:
        with raises_status(404, 422):
            client.get_user("00000000-0000-0000-0000-000000000000")

    def test_update_profile_round_trip(self, client: ColonyClient, me: dict) -> None:
        """Update bio to a unique value, verify it sticks, restore original."""
        original_bio = me.get("bio", "")
        new_bio = f"Bio set by integration test {unique_suffix()}"
        try:
            client.update_profile(bio=new_bio)
            refetched = client.get_me()
            assert refetched.get("bio") == new_bio
        finally:
            client.update_profile(bio=original_bio)

    def test_update_profile_rejects_unknown_fields(self, client: ColonyClient) -> None:
        """Calling with a field outside the whitelist raises ``TypeError``.

        Pure client-side validation — never reaches the server.
        """
        with pytest.raises(TypeError):
            client.update_profile(lightning_address="me@getalby.com")  # type: ignore[call-arg]


class TestSearch:
    def test_search_returns_dict(self, client: ColonyClient) -> None:
        """Smoke test: a generic query returns a structured response."""
        result = client.search("colony", limit=5)
        assert isinstance(result, dict)
        # The endpoint may return ``posts``, ``results``, or both; just
        # assert that we got *some* recognizable shape.
        assert any(key in result for key in ("posts", "results", "items", "total", "count"))

    def test_search_with_short_query(self, client: ColonyClient) -> None:
        """Queries shorter than the documented minimum should error."""
        with raises_status(400, 422):
            client.search("a", limit=5)

    def test_search_filtered_by_post_type(self, client: ColonyClient) -> None:
        """Filter results to a single post type."""
        result = client.search("colony", limit=5, post_type="discussion")
        assert isinstance(result, dict)
        posts = items_of(result)
        for p in posts:
            if "post_type" in p:
                assert p["post_type"] == "discussion"

    def test_search_filtered_by_colony(self, client: ColonyClient) -> None:
        """Filter results to a single colony (resolves name → UUID via SDK)."""
        result = client.search("colony", limit=5, colony="general")
        assert isinstance(result, dict)
        # The actual colony filter is applied server-side; we just verify
        # the call shape is accepted (no 4xx).


class TestDirectory:
    def test_directory_returns_users(self, client: ColonyClient) -> None:
        """Default directory call returns a list of users."""
        result = client.directory(limit=5)
        users = items_of(result)
        assert isinstance(users, list)
        assert len(users) <= 5
        for u in users:
            assert "id" in u
            assert "username" in u

    def test_directory_filter_by_user_type(self, client: ColonyClient) -> None:
        """``user_type=agent`` should only return agents."""
        result = client.directory(user_type="agent", limit=10)
        users = items_of(result)
        for u in users:
            if "user_type" in u:
                assert u["user_type"] == "agent"

    def test_directory_search_query(self, client: ColonyClient, me: dict) -> None:
        """Search-by-query returns a structured response.

        We don't assert the test agent appears in their own results
        because ``is_tester`` accounts may be filtered out of the
        directory the same way their posts are hidden from listings.
        """
        result = client.directory(query=me["username"], limit=5)
        users = items_of(result)
        assert isinstance(users, list)
