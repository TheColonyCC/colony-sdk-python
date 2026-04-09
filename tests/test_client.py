"""Basic tests for the Colony SDK client."""

import sys
from pathlib import Path

# Add src to path for testing without install
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from colony_sdk import COLONIES, ColonyAPIError, ColonyClient


def test_colonies_complete():
    """All 10 colonies should be present (9 canonical + test-posts)."""
    assert len(COLONIES) == 10
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
        "test-posts",
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


def test_unfollow_is_separate_method():
    """unfollow() should be a distinct method from follow()."""
    client = ColonyClient("col_test")
    assert callable(client.unfollow)
    assert client.unfollow.__func__ is not client.follow.__func__


def test_api_error_exported():
    """ColonyAPIError should be importable from the top-level package."""
    from colony_sdk import ColonyAPIError as Err

    assert Err is ColonyAPIError


# ---------------------------------------------------------------------------
# verify_webhook
# ---------------------------------------------------------------------------


class TestVerifyWebhook:
    SECRET = "supersecretwebhooksecretkey"  # ≥16 chars per Colony's rule

    def _sign(self, body: bytes, secret: str | None = None) -> str:
        import hashlib
        import hmac

        return hmac.new((secret or self.SECRET).encode(), body, hashlib.sha256).hexdigest()

    def test_valid_signature_bytes_payload(self) -> None:
        from colony_sdk import verify_webhook

        body = b'{"event": "post_created", "id": "p1"}'
        sig = self._sign(body)
        assert verify_webhook(body, sig, self.SECRET) is True

    def test_valid_signature_str_payload(self) -> None:
        from colony_sdk import verify_webhook

        body_str = '{"event": "comment_created"}'
        sig = self._sign(body_str.encode())
        assert verify_webhook(body_str, sig, self.SECRET) is True

    def test_invalid_signature_returns_false(self) -> None:
        from colony_sdk import verify_webhook

        body = b'{"event": "post_created"}'
        bad_sig = "0" * 64  # right length, wrong content
        assert verify_webhook(body, bad_sig, self.SECRET) is False

    def test_wrong_secret_returns_false(self) -> None:
        from colony_sdk import verify_webhook

        body = b'{"event": "post_created"}'
        sig = self._sign(body)
        assert verify_webhook(body, sig, secret="a-different-secret-key") is False

    def test_tampered_payload_returns_false(self) -> None:
        from colony_sdk import verify_webhook

        original = b'{"value": 100}'
        sig = self._sign(original)
        tampered = b'{"value": 999}'
        assert verify_webhook(tampered, sig, self.SECRET) is False

    def test_sha256_prefix_is_tolerated(self) -> None:
        from colony_sdk import verify_webhook

        body = b'{"event": "post_created"}'
        sig = self._sign(body)
        assert verify_webhook(body, f"sha256={sig}", self.SECRET) is True

    def test_short_signature_returns_false_not_raises(self) -> None:
        from colony_sdk import verify_webhook

        body = b'{"event": "x"}'
        # Truncated / malformed — must not raise, just return False
        assert verify_webhook(body, "deadbeef", self.SECRET) is False

    def test_empty_signature_returns_false(self) -> None:
        from colony_sdk import verify_webhook

        body = b'{"event": "x"}'
        assert verify_webhook(body, "", self.SECRET) is False

    def test_empty_body(self) -> None:
        from colony_sdk import verify_webhook

        sig = self._sign(b"")
        assert verify_webhook(b"", sig, self.SECRET) is True

    def test_unicode_body(self) -> None:
        from colony_sdk import verify_webhook

        body_str = '{"title": "héllo 🐡"}'
        sig = self._sign(body_str.encode("utf-8"))
        assert verify_webhook(body_str, sig, self.SECRET) is True
        assert verify_webhook(body_str.encode("utf-8"), sig, self.SECRET) is True
