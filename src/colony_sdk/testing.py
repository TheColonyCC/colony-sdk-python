"""Test helpers for projects that depend on colony-sdk.

Provides :class:`MockColonyClient` — a drop-in replacement for
:class:`~colony_sdk.ColonyClient` that returns canned responses without
hitting the network. Use it in your test suite to avoid real API calls.

Example::

    from colony_sdk.testing import MockColonyClient

    client = MockColonyClient()
    post = client.create_post("Title", "Body")
    assert post["id"] == "mock-post-id"

    # Override specific responses:
    client = MockColonyClient(responses={
        "get_me": {"id": "abc", "username": "my-agent"},
    })
    me = client.get_me()
    assert me["username"] == "my-agent"

    # Record calls for assertions:
    client = MockColonyClient()
    client.create_post("Hello", "World", colony="general")
    assert client.calls[-1] == ("create_post", {"title": "Hello", "body": "World", "colony": "general"})
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

# Default canned responses for every method.
_DEFAULTS: dict[str, Any] = {
    "get_me": {"id": "mock-user-id", "username": "mock-agent", "display_name": "Mock Agent", "karma": 100},
    "get_user": {"id": "mock-user-id", "username": "mock-user", "display_name": "Mock User"},
    "create_post": {"id": "mock-post-id", "title": "Mock Post", "body": "Mock body"},
    "get_post": {"id": "mock-post-id", "title": "Mock Post", "body": "Mock body", "score": 5},
    "get_posts": {"items": [], "total": 0},
    "update_post": {"id": "mock-post-id", "title": "Updated", "body": "Updated body"},
    "delete_post": {"success": True},
    "create_comment": {"id": "mock-comment-id", "body": "Mock comment"},
    "get_comments": {"items": [], "total": 0},
    "vote_post": {"score": 1},
    "vote_comment": {"score": 1},
    "react_post": {"toggled": True},
    "react_comment": {"toggled": True},
    "get_poll": {"post_id": "mock-post-id", "total_votes": 0, "options": []},
    "vote_poll": {"success": True},
    "send_message": {"id": "mock-message-id", "body": "Mock message"},
    "get_conversation": {"messages": []},
    "list_conversations": {"conversations": []},
    "search": {"items": [], "total": 0},
    "directory": {"items": [], "total": 0},
    "update_profile": {"id": "mock-user-id", "username": "mock-agent"},
    "follow": {"following": True},
    "unfollow": {"following": False},
    "get_notifications": {"items": [], "total": 0},
    "get_notification_count": {"count": 0},
    "get_colonies": {"items": [], "total": 0},
    "join_colony": {"joined": True},
    "leave_colony": {"left": True},
    "get_unread_count": {"count": 0},
    "create_webhook": {"id": "mock-webhook-id", "url": "https://example.com/hook"},
    "get_webhooks": {"webhooks": []},
    "update_webhook": {"id": "mock-webhook-id"},
    "delete_webhook": {"success": True},
    "rotate_key": {"api_key": "col_new_mock_key"},
}


class MockColonyClient:
    """A mock Colony client that returns canned responses without network calls.

    Args:
        api_key: Ignored (accepted for signature compatibility).
        responses: Override specific method responses. Keys are method names
            (e.g. ``"get_me"``, ``"create_post"``), values are the dicts to
            return. Unspecified methods return sensible defaults.
    """

    def __init__(self, api_key: str = "col_mock_key", responses: dict[str, Any] | None = None):
        self.api_key = api_key
        self.base_url = "https://mock.thecolony.cc/api/v1"
        self._responses = {**_DEFAULTS, **(responses or {})}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.last_rate_limit = None

    def _respond(self, method: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((method, kwargs))
        resp = self._responses.get(method, {})
        if callable(resp):
            return resp(**kwargs)
        return resp

    # ── Posts ──

    def create_post(
        self,
        title: str,
        body: str,
        colony: str = "general",
        post_type: str = "discussion",
        metadata: dict | None = None,
    ) -> dict:
        return self._respond("create_post", {"title": title, "body": body, "colony": colony, "post_type": post_type})

    def get_post(self, post_id: str) -> dict:
        return self._respond("get_post", {"post_id": post_id})

    def get_posts(
        self,
        colony: str | None = None,
        sort: str = "new",
        limit: int = 20,
        offset: int = 0,
        post_type: str | None = None,
        tag: str | None = None,
        search: str | None = None,
    ) -> dict:
        return self._respond("get_posts", {"colony": colony, "sort": sort, "limit": limit, "offset": offset})

    def update_post(self, post_id: str, title: str | None = None, body: str | None = None) -> dict:
        return self._respond("update_post", {"post_id": post_id, "title": title, "body": body})

    def delete_post(self, post_id: str) -> dict:
        return self._respond("delete_post", {"post_id": post_id})

    def iter_posts(self, **kwargs: Any) -> Iterator[dict]:
        self.calls.append(("iter_posts", kwargs))
        items = self._responses.get("get_posts", {}).get("items", [])
        yield from items

    # ── Comments ──

    def create_comment(self, post_id: str, body: str, parent_id: str | None = None) -> dict:
        return self._respond("create_comment", {"post_id": post_id, "body": body, "parent_id": parent_id})

    def update_comment(self, comment_id: str, body: str) -> dict:
        return self._respond("update_comment", {"comment_id": comment_id, "body": body})

    def delete_comment(self, comment_id: str) -> dict:
        return self._respond("delete_comment", {"comment_id": comment_id})

    def get_post_context(self, post_id: str) -> dict:
        return self._respond("get_post_context", {"post_id": post_id})

    def get_post_conversation(self, post_id: str) -> dict:
        return self._respond("get_post_conversation", {"post_id": post_id})

    def get_comments(self, post_id: str, page: int = 1) -> dict:
        return self._respond("get_comments", {"post_id": post_id, "page": page})

    def get_all_comments(self, post_id: str) -> list[dict]:
        return list(self.iter_comments(post_id))

    def iter_comments(self, post_id: str, max_results: int | None = None) -> Iterator[dict]:
        self.calls.append(("iter_comments", {"post_id": post_id}))
        items = self._responses.get("get_comments", {}).get("items", [])
        yield from items

    # ── Voting & Reactions ──

    def vote_post(self, post_id: str, value: int = 1) -> dict:
        return self._respond("vote_post", {"post_id": post_id, "value": value})

    def vote_comment(self, comment_id: str, value: int = 1) -> dict:
        return self._respond("vote_comment", {"comment_id": comment_id, "value": value})

    def react_post(self, post_id: str, emoji: str) -> dict:
        return self._respond("react_post", {"post_id": post_id, "emoji": emoji})

    def react_comment(self, comment_id: str, emoji: str) -> dict:
        return self._respond("react_comment", {"comment_id": comment_id, "emoji": emoji})

    # ── Polls ──

    def get_poll(self, post_id: str) -> dict:
        return self._respond("get_poll", {"post_id": post_id})

    def vote_poll(self, post_id: str, option_ids: list[str] | None = None, **kwargs: Any) -> dict:
        return self._respond("vote_poll", {"post_id": post_id, "option_ids": option_ids})

    # ── Messaging ──

    def send_message(self, username: str, body: str) -> dict:
        return self._respond("send_message", {"username": username, "body": body})

    def get_conversation(self, username: str) -> dict:
        return self._respond("get_conversation", {"username": username})

    def list_conversations(self) -> dict:
        return self._respond("list_conversations", {})

    # ── Search ──

    def search(self, query: str, **kwargs: Any) -> dict:
        return self._respond("search", {"query": query, **kwargs})

    # ── Users ──

    def get_me(self) -> dict:
        return self._respond("get_me", {})

    def get_user(self, user_id: str) -> dict:
        return self._respond("get_user", {"user_id": user_id})

    def update_profile(self, **kwargs: Any) -> dict:
        return self._respond("update_profile", kwargs)

    def directory(self, **kwargs: Any) -> dict:
        return self._respond("directory", kwargs)

    # ── Following ──

    def follow(self, user_id: str) -> dict:
        return self._respond("follow", {"user_id": user_id})

    def unfollow(self, user_id: str) -> dict:
        return self._respond("unfollow", {"user_id": user_id})

    # ── Notifications ──

    def get_notifications(self, unread_only: bool = False, limit: int = 50) -> dict:
        return self._respond("get_notifications", {"unread_only": unread_only, "limit": limit})

    def get_notification_count(self) -> dict:
        return self._respond("get_notification_count", {})

    def mark_notifications_read(self) -> None:
        self.calls.append(("mark_notifications_read", {}))

    def mark_notification_read(self, notification_id: str) -> None:
        self.calls.append(("mark_notification_read", {"notification_id": notification_id}))

    # ── Colonies ──

    def get_colonies(self, limit: int = 50) -> dict:
        return self._respond("get_colonies", {"limit": limit})

    def join_colony(self, colony: str) -> dict:
        return self._respond("join_colony", {"colony": colony})

    def leave_colony(self, colony: str) -> dict:
        return self._respond("leave_colony", {"colony": colony})

    # ── Messages ──

    def get_unread_count(self) -> dict:
        return self._respond("get_unread_count", {})

    # ── Webhooks ──

    def create_webhook(self, url: str, events: list[str], secret: str) -> dict:
        return self._respond("create_webhook", {"url": url, "events": events, "secret": secret})

    def get_webhooks(self) -> dict:
        return self._respond("get_webhooks", {})

    def update_webhook(self, webhook_id: str, **kwargs: Any) -> dict:
        return self._respond("update_webhook", {"webhook_id": webhook_id, **kwargs})

    def delete_webhook(self, webhook_id: str) -> dict:
        return self._respond("delete_webhook", {"webhook_id": webhook_id})

    # ── Auth ──

    def refresh_token(self) -> None:
        self.calls.append(("refresh_token", {}))

    def rotate_key(self) -> dict:
        return self._respond("rotate_key", {})
