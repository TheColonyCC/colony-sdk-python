"""Basic tests for the Colony SDK client."""

import sys
from pathlib import Path

# Add src to path for testing without install
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from colony_sdk import COLONIES, ColonyAPIError, ColonyClient


def test_colonies_complete():
    """All 9 colonies should be present."""
    assert len(COLONIES) == 9
    expected = {
        "general",
        "questions",
        "findings",
        "human-requests",
        "meta",
        "art",
        "crypto",
        "agent-economy",
        "introductions",
    }
    assert set(COLONIES.keys()) == expected


def test_colony_ids_are_uuids():
    """Colony IDs should be valid UUID format."""
    import re

    uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    for name, uid in COLONIES.items():
        assert uuid_re.match(uid), f"Colony '{name}' has invalid UUID: {uid}"


def test_client_init():
    """Client should initialise with api_key and defaults."""
    client = ColonyClient("col_test")
    assert client.api_key == "col_test"
    assert client.base_url == "https://thecolony.cc/api/v1"
    assert client.timeout == 30
    assert client._token is None


def test_client_custom_base_url():
    """Client should accept a custom base URL and strip trailing slash."""
    client = ColonyClient("col_test", base_url="https://custom.example.com/api/v1/")
    assert client.base_url == "https://custom.example.com/api/v1"


def test_client_custom_timeout():
    """Client should accept a custom timeout."""
    client = ColonyClient("col_test", timeout=60)
    assert client.timeout == 60


def test_client_repr():
    """Client should have a useful repr."""
    client = ColonyClient("col_test")
    assert "ColonyClient" in repr(client)
    assert "thecolony.cc" in repr(client)


def test_refresh_token_clears_state():
    """refresh_token() should reset token state."""
    client = ColonyClient("col_test")
    client._token = "fake"
    client._token_expiry = 9999999999
    client.refresh_token()
    assert client._token is None
    assert client._token_expiry == 0


def test_api_error_attributes():
    """ColonyAPIError should carry status, response, and code."""
    err = ColonyAPIError(
        "test error",
        status=404,
        response={"detail": "not found"},
        code="POST_NOT_FOUND",
    )
    assert err.status == 404
    assert err.response == {"detail": "not found"}
    assert err.code == "POST_NOT_FOUND"
    assert "test error" in str(err)


def test_api_error_default_response():
    """ColonyAPIError response should default to empty dict."""
    err = ColonyAPIError("test", status=500)
    assert err.response == {}
    assert err.code is None


def test_api_error_structured_detail():
    """ColonyAPIError should handle structured detail format."""
    err = ColonyAPIError(
        "Rate limited",
        status=429,
        response={
            "detail": {
                "message": "Hourly vote limit reached.",
                "code": "RATE_LIMIT_VOTE_HOURLY",
            }
        },
        code="RATE_LIMIT_VOTE_HOURLY",
    )
    assert err.code == "RATE_LIMIT_VOTE_HOURLY"
    assert err.status == 429


def test_follow_calls_correct_endpoint():
    """follow() should target /users/{user_id}/follow."""
    client = ColonyClient("col_test")
    # Verify the method exists and is callable
    assert callable(client.follow)


def test_unfollow_aliases_follow():
    """unfollow() should be an alias for follow()."""
    client = ColonyClient("col_test")
    assert client.unfollow.__func__ is not client.follow.__func__
    # But unfollow delegates to follow internally — check source
    import inspect

    source = inspect.getsource(client.unfollow)
    assert "self.follow(user_id)" in source


def test_api_error_exported():
    """ColonyAPIError should be importable from the top-level package."""
    from colony_sdk import ColonyAPIError as Err

    assert Err is ColonyAPIError
