"""Integration tests for profile, user lookup, and search."""

from __future__ import annotations

import pytest

from colony_sdk import ColonyAPIError, ColonyClient, ColonyNotFoundError

from .conftest import unique_suffix


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
        with pytest.raises((ColonyNotFoundError, ColonyAPIError)) as exc_info:
            client.get_user("00000000-0000-0000-0000-000000000000")
        assert exc_info.value.status in (404, 422)

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
        with pytest.raises(ColonyAPIError):
            client.search("a", limit=5)
