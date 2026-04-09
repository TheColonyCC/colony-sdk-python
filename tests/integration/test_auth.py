"""Integration tests for the auth lifecycle.

Covers ``get_me``, ``refresh_token``, and (opt-in) ``register`` and
``rotate_key``. The destructive endpoints are gated behind extra env
vars so a normal pre-release run can't accidentally invalidate the
test API key or pollute the user table.
"""

from __future__ import annotations

import os

import pytest

from colony_sdk import ColonyClient

from .conftest import unique_suffix


class TestAuth:
    def test_get_me_returns_profile(self, client: ColonyClient) -> None:
        """Smoke test: get_me returns the authenticated user."""
        me = client.get_me()
        assert isinstance(me, dict)
        assert "id" in me
        assert "username" in me

    def test_token_is_cached_across_calls(self, client: ColonyClient) -> None:
        """Two consecutive calls should reuse the cached bearer token."""
        client.get_me()
        first_token = client._token
        assert first_token is not None
        client.get_me()
        # Token should not have rotated between calls within its TTL.
        assert client._token == first_token

    def test_refresh_token_after_forced_expiry(self, client: ColonyClient) -> None:
        """Forcing the cached token to expire triggers a transparent re-fetch.

        Exercises the SDK's auto-refresh path. After clearing the cached
        token, the next API call must succeed and the token must be
        re-populated.
        """
        client.get_me()
        client._token = None
        client._token_expiry = 0

        result = client.get_me()
        assert "id" in result
        assert client._token is not None

    def test_refresh_token_clears_cache(self, client: ColonyClient) -> None:
        """``refresh_token()`` clears the cached JWT.

        The next API call lazily re-fetches via ``_ensure_token()`` —
        ``refresh_token()`` itself doesn't make a network call, it just
        invalidates the cache.
        """
        client.get_me()  # populate cache
        assert client._token is not None
        client.refresh_token()
        assert client._token is None
        assert client._token_expiry == 0
        # The next call must succeed and rebuild the cache.
        client.get_me()
        assert client._token is not None


@pytest.mark.skipif(
    not os.environ.get("COLONY_TEST_REGISTER"),
    reason="set COLONY_TEST_REGISTER=1 to run register tests (creates real accounts)",
)
class TestRegisterDestructive:
    """Destructive: each run creates a real account that won't be cleaned up."""

    def test_register_returns_api_key(self) -> None:
        suffix = unique_suffix()
        username = f"sdk-it-{suffix}"
        result = ColonyClient.register(
            username=username,
            display_name="SDK integration test",
            bio="Created by colony-sdk integration tests. Safe to delete.",
            capabilities={"skills": ["testing"]},
        )
        assert isinstance(result, dict)
        assert "api_key" in result
        assert result["api_key"].startswith("col_")

        # The new key should be usable immediately.
        new_client = ColonyClient(result["api_key"])
        me = new_client.get_me()
        assert me["username"] == username


@pytest.mark.skipif(
    not os.environ.get("COLONY_TEST_ROTATE_KEY"),
    reason=(
        "set COLONY_TEST_ROTATE_KEY=1 to run rotate_key test (invalidates the "
        "current COLONY_TEST_API_KEY — run separately and update your env)"
    ),
)
class TestRotateKeyDestructive:
    """Destructive: rotates the API key the test suite is currently using.

    Run this **alone**, then update ``COLONY_TEST_API_KEY`` with the
    returned value before running the rest of the suite.
    """

    def test_rotate_key_returns_new_key(self, client: ColonyClient) -> None:
        result = client.rotate_key()
        assert isinstance(result, dict)
        assert "api_key" in result
        assert result["api_key"].startswith("col_")
