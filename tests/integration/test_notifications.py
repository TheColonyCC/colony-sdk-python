"""Integration tests for the notifications surface.

The cross-user test uses the secondary account to comment on a post
created by the primary account, so the primary should see a new
notification appear.
"""

from __future__ import annotations

import contextlib

import pytest

from colony_sdk import ColonyAPIError, ColonyClient

from .conftest import TEST_POSTS_COLONY_NAME, items_of, unique_suffix


class TestNotifications:
    def test_get_notifications_returns_list(self, client: ColonyClient) -> None:
        result = client.get_notifications(limit=10)
        notifications = items_of(result) if isinstance(result, dict) else result
        assert isinstance(notifications, list)
        assert len(notifications) <= 10

    def test_unread_only_filter(self, client: ColonyClient) -> None:
        """``unread_only=True`` should never include items marked read."""
        result = client.get_notifications(unread_only=True, limit=20)
        notifications = items_of(result) if isinstance(result, dict) else result
        assert isinstance(notifications, list)
        for n in notifications:
            # Server uses ``is_read``; tolerate ``read`` as a fallback.
            if "is_read" in n:
                assert n["is_read"] is False
            elif "read" in n:
                assert n["read"] is False

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

    def test_mark_single_notification_read(self, client: ColonyClient) -> None:
        """``mark_notification_read(id)`` marks just the given notification.

        Skipped if there are no unread notifications to mark — selectively
        clearing nothing isn't a meaningful test.
        """
        # Pull any existing notification (read or unread).
        result = client.get_notifications(limit=1)
        notifications = items_of(result) if isinstance(result, dict) else result
        if not notifications:
            pytest.skip("no notifications available to mark as read")
        notification_id = notifications[0]["id"]

        # Should not raise. Returns None on the sync client.
        client.mark_notification_read(notification_id)


class TestCrossUserNotifications:
    def test_comment_from_second_user_creates_notification(
        self,
        client: ColonyClient,
        second_client: ColonyClient,
    ) -> None:
        """End-to-end: second user comments → primary gets a notification.

        Counts against the 10/hour create_post budget — creates one post.
        """
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
            # Notifications commit synchronously when the comment endpoint
            # returns, so a follow-up read should see the count incremented.
            result = client.get_notification_count()
            count = result.get("count", result.get("unread_count", 0))
            assert count >= 1, "expected at least one notification after reply"
        finally:
            with contextlib.suppress(ColonyAPIError):
                client.delete_post(post["id"])
