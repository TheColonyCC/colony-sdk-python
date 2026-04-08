"""Integration tests for webhook endpoints.

These tests hit the real Colony API and require a valid API key.

Run with:
    COLONY_TEST_API_KEY=col_xxx pytest tests/test_integration_webhooks.py -v

Skipped automatically when the env var is not set.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from colony_sdk import ColonyAPIError, ColonyClient

API_KEY = os.environ.get("COLONY_TEST_API_KEY")

pytestmark = pytest.mark.skipif(not API_KEY, reason="set COLONY_TEST_API_KEY to run")


@pytest.fixture
def client() -> ColonyClient:
    assert API_KEY is not None
    return ColonyClient(API_KEY)


class TestWebhooksIntegration:
    def test_webhook_lifecycle(self, client: ColonyClient) -> None:
        """Create, list, and delete a webhook against the real API."""
        # Create
        result = client.create_webhook(
            url="https://example.com/integration-test-hook",
            events=["post_created", "mention"],
            secret="integration-test-secret-key-0123",
        )
        assert "id" in result
        assert result["url"] == "https://example.com/integration-test-hook"
        assert result["events"] == ["post_created", "mention"]
        assert result["is_active"] is True
        webhook_id = result["id"]

        try:
            # List — should contain the new webhook
            webhooks = client.get_webhooks()
            assert isinstance(webhooks, list)
            ids = [wh["id"] for wh in webhooks]
            assert webhook_id in ids
        finally:
            # Always clean up
            client.delete_webhook(webhook_id)

        # Verify deleted
        webhooks = client.get_webhooks()
        ids = [wh["id"] for wh in webhooks]
        assert webhook_id not in ids

    def test_delete_nonexistent_webhook_raises(self, client: ColonyClient) -> None:
        """Deleting a nonexistent webhook should raise ColonyAPIError."""
        with pytest.raises(ColonyAPIError) as exc_info:
            client.delete_webhook("00000000-0000-0000-0000-000000000000")
        assert exc_info.value.status == 404
