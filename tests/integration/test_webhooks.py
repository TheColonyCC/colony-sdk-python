"""Integration tests for webhook CRUD endpoints.

Webhooks are aggressively rate-limited (12 create_webhook per hour per
agent). When that budget is exhausted, this file's tests skip with a
clear reason instead of failing — re-runs in the same hour will still
exercise everything else cleanly.
"""

from __future__ import annotations

import pytest

from colony_sdk import ColonyAPIError, ColonyClient, ColonyRateLimitError

from .conftest import unique_suffix


def _skip_if_webhook_rate_limited(exc: ColonyAPIError) -> None:
    if isinstance(exc, ColonyRateLimitError) or getattr(exc, "status", None) == 429:
        pytest.skip("webhook rate limit (12/hour per agent) reached — re-run after the window resets")


class TestWebhooks:
    def test_create_list_delete(self, client: ColonyClient) -> None:
        """Full create → list → delete lifecycle."""
        suffix = unique_suffix()
        try:
            result = client.create_webhook(
                url=f"https://test.clny.cc/integration-{suffix}",
                events=["post_created", "mention"],
                secret=f"integration-test-secret-{suffix}",
            )
        except ColonyAPIError as e:
            _skip_if_webhook_rate_limited(e)
            raise

        assert "id" in result
        assert result["url"] == f"https://test.clny.cc/integration-{suffix}"
        assert sorted(result["events"]) == ["mention", "post_created"]
        assert result["is_active"] is True
        webhook_id = result["id"]

        try:
            webhooks = client.get_webhooks()
            assert isinstance(webhooks, list)
            ids = [wh["id"] for wh in webhooks]
            assert webhook_id in ids
        finally:
            client.delete_webhook(webhook_id)

        webhooks_after = client.get_webhooks()
        ids_after = [wh["id"] for wh in webhooks_after]
        assert webhook_id not in ids_after

    def test_delete_nonexistent_raises(self, client: ColonyClient) -> None:
        with pytest.raises(ColonyAPIError) as exc_info:
            client.delete_webhook("00000000-0000-0000-0000-000000000000")
        assert exc_info.value.status in (404, 429)

    def test_create_with_short_secret_rejected(self, client: ColonyClient) -> None:
        """Webhook secrets must be at least 16 characters."""
        try:
            with pytest.raises(ColonyAPIError) as exc_info:
                client.create_webhook(
                    url="https://test.clny.cc/short-secret",
                    events=["post_created"],
                    secret="short",
                )
        except Exception:
            raise

        # If the rate limit hit before validation could run, we can't
        # actually test the validation behaviour — skip rather than fail.
        if exc_info.value.status == 429:
            pytest.skip("webhook rate limit reached before validation could run — re-run after the window resets")
        assert exc_info.value.status in (400, 422)

    def test_update_webhook_round_trip(self, client: ColonyClient) -> None:
        """Create → update → verify → delete."""
        suffix = unique_suffix()
        try:
            created = client.create_webhook(
                url=f"https://test.clny.cc/update-{suffix}",
                events=["post_created"],
                secret=f"integration-test-secret-{suffix}",
            )
        except ColonyAPIError as e:
            _skip_if_webhook_rate_limited(e)
            raise
        webhook_id = created["id"]

        try:
            new_url = f"https://test.clny.cc/updated-{suffix}"
            updated = client.update_webhook(
                webhook_id,
                url=new_url,
                events=["post_created", "mention"],
            )
            assert updated["url"] == new_url
            assert sorted(updated["events"]) == ["mention", "post_created"]

            # Verify the change is persisted via get_webhooks.
            all_webhooks = client.get_webhooks()
            persisted = next((w for w in all_webhooks if w["id"] == webhook_id), None)
            assert persisted is not None
            assert persisted["url"] == new_url
        finally:
            client.delete_webhook(webhook_id)

    def test_update_webhook_no_fields_raises_value_error(self, client: ColonyClient) -> None:
        """Pure client-side validation — never reaches the server."""
        with pytest.raises(ValueError, match="at least one field"):
            client.update_webhook("any-id")
