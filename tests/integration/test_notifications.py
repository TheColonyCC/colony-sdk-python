"""Integration tests for the notifications surface.

The cross-user test uses the secondary account to comment on a post
created by the primary account, so the primary should see a new
notification appear.
"""

from __future__ import annotations

import pytest

from colony_sdk import ColonyClient

from .conftest import TEST_POSTS_COLONY_NAME, unique_suffix


class TestNotifications:
    def test_get_notifications_returns_list(self, client: ColonyClient) -> None:
        result = client.get_notifications(limit=10)
        notifications = result.get("notifications", result) if isinstance(result, dict) else result
        assert isinstance(notifications, list)
        assert len(notifications) <= 10

    def test_unread_only_filter(self, client: ColonyClient) -> None:
        """``unread_only=True`` should never include items marked read."""
        result = client.get_notifications(unread_only=True, limit=20)
        notifications = result.get("notifications", result) if isinstance(result, dict) else result
        assert isinstance(notifications, list)
        for n in notifications:
            if "read" in n:
                assert n["read"] is False
            elif "is_read" in n:
                assert n["is_read"] is False

    def test_get_notification_count(self, client: ColonyClient) -> None:
        result = client.get_notification_count()
        assert isinstance(result, dict)
        count = result.get("count", result.get("unread_count", 0))
        assert isinstance(count, int)
        assert count >= 0

    def test_mark_notifications_read_clears_count(self, client: ColonyClient) -> None:
        """After ``mark_notifications_read``, unread count should be 0."""
        client.mark_notifications_read()
        result = client.get_notification_count()
        count = result.get("count", result.get("unread_count", 0))
        assert count == 0


class TestCrossUserNotifications:
    def test_comment_from_second_user_creates_notification(
        self,
        client: ColonyClient,
        second_client: ColonyClient,
    ) -> None:
        """End-to-end: second user comments → primary gets a notification."""
        # Start from a clean slate.
        client.mark_notifications_read()

        suffix = unique_suffix()
        post = client.create_post(
            title=f"Notification end-to-end {suffix}",
            body="Triggers a reply from the second test user.",
            colony=TEST_POSTS_COLONY_NAME,
            post_type="discussion",
        )
        try:
            second_client.create_comment(post["id"], f"Reply from second user {suffix}.")

            # Notification arrival can be slightly delayed; one re-check is
            # plenty in practice but we won't sleep here — the API call
            # itself is synchronous and the server commits before responding.
            result = client.get_notification_count()
            count = result.get("count", result.get("unread_count", 0))
            assert count >= 1, "expected at least one notification after reply"
        finally:
            try:
                client.delete_post(post["id"])
            except Exception:
                pytest.skip("test post cleanup failed (edit window closed?)")
