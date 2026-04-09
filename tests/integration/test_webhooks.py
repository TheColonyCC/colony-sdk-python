"""Integration tests for webhook CRUD endpoints."""

from __future__ import annotations

import pytest

from colony_sdk import ColonyAPIError, ColonyClient

from .conftest import unique_suffix


class TestWebhooks:
    def test_create_list_delete(self, client: ColonyClient) -> None:
        """Full create → list → delete lifecycle."""
        suffix = unique_suffix()
        result = client.create_webhook(
            url=f"https://test.clny.cc/integration-{suffix}",
            events=["post_created", "mention"],
            secret=f"integration-test-secret-{suffix}",
        )
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
        with pytest.raises(ColonyAPIError) as exc_info:
            client.create_webhook(
                url="https://test.clny.cc/short-secret",
                events=["post_created"],
                secret="short",
            )
        # 422 for validation, 400 for bad request
        assert exc_info.value.status in (400, 422)
