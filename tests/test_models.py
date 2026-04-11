"""Tests for colony_sdk.models — typed response models."""

from colony_sdk.models import (
    Colony,
    Comment,
    Message,
    Notification,
    PollResults,
    Post,
    RateLimitInfo,
    User,
    Webhook,
)


class TestUser:
    def test_from_dict_minimal(self) -> None:
        u = User.from_dict({"id": "abc", "username": "agent1"})
        assert u.id == "abc"
        assert u.username == "agent1"
        assert u.karma == 0
        assert u.capabilities == {}

    def test_from_dict_full(self) -> None:
        u = User.from_dict(
            {
                "id": "abc",
                "username": "agent1",
                "display_name": "Agent One",
                "bio": "I'm an agent",
                "user_type": "agent",
                "karma": 42,
                "post_count": 10,
                "comment_count": 5,
                "capabilities": {"skills": ["python"]},
                "created_at": "2026-01-01T00:00:00Z",
                "avatar_url": "https://example.com/avatar.png",
                "is_following": True,
            }
        )
        assert u.karma == 42
        assert u.capabilities == {"skills": ["python"]}
        assert u.is_following is True

    def test_roundtrip(self) -> None:
        d = {"id": "abc", "username": "agent1", "karma": 10}
        u = User.from_dict(d)
        result = u.to_dict()
        assert result["id"] == "abc"
        assert result["karma"] == 10

    def test_user_id_fallback(self) -> None:
        u = User.from_dict({"user_id": "xyz", "username": "test"})
        assert u.id == "xyz"

    def test_frozen(self) -> None:
        import pytest

        u = User.from_dict({"id": "abc", "username": "test"})
        with pytest.raises(AttributeError):
            u.id = "new"  # type: ignore[misc]


class TestPost:
    def test_from_dict_with_author(self) -> None:
        p = Post.from_dict(
            {
                "id": "post1",
                "title": "Hello",
                "body": "World",
                "author": {"id": "u1", "username": "agent1"},
                "score": 5,
                "tags": ["python", "ai"],
            }
        )
        assert p.author_id == "u1"
        assert p.author_username == "agent1"
        assert p.tags == ["python", "ai"]

    def test_from_dict_flat_author(self) -> None:
        p = Post.from_dict(
            {
                "id": "post1",
                "title": "Hello",
                "body": "World",
                "author_id": "u1",
                "author_username": "agent1",
            }
        )
        assert p.author_id == "u1"

    def test_roundtrip(self) -> None:
        d = {"id": "p1", "title": "T", "body": "B", "score": 3}
        p = Post.from_dict(d)
        result = p.to_dict()
        assert result["score"] == 3


class TestComment:
    def test_from_dict(self) -> None:
        c = Comment.from_dict(
            {
                "id": "c1",
                "body": "Great post!",
                "post_id": "p1",
                "author": {"id": "u1", "username": "agent1"},
                "parent_id": "c0",
                "score": 2,
            }
        )
        assert c.id == "c1"
        assert c.parent_id == "c0"
        assert c.author_username == "agent1"

    def test_roundtrip(self) -> None:
        c = Comment.from_dict({"id": "c1", "body": "test"})
        d = c.to_dict()
        assert d["id"] == "c1"
        assert "parent_id" not in d  # None fields excluded


class TestMessage:
    def test_from_dict(self) -> None:
        m = Message.from_dict(
            {
                "id": "m1",
                "body": "Hello!",
                "sender": {"id": "u1", "username": "alice"},
                "recipient": {"id": "u2", "username": "bob"},
                "read": True,
            }
        )
        assert m.sender_username == "alice"
        assert m.recipient_username == "bob"
        assert m.read is True


class TestNotification:
    def test_from_dict(self) -> None:
        n = Notification.from_dict(
            {
                "id": "n1",
                "type": "reply",
                "message": "Someone replied",
                "read": False,
                "post_id": "p1",
                "from_username": "agent2",
            }
        )
        assert n.type == "reply"
        assert n.post_id == "p1"

    def test_to_dict_excludes_none(self) -> None:
        n = Notification.from_dict({"id": "n1", "type": "mention"})
        d = n.to_dict()
        assert "post_id" not in d
        assert "comment_id" not in d


class TestColony:
    def test_from_dict(self) -> None:
        c = Colony.from_dict(
            {
                "id": "col1",
                "name": "general",
                "description": "General discussion",
                "member_count": 100,
            }
        )
        assert c.name == "general"
        assert c.member_count == 100


class TestWebhook:
    def test_from_dict(self) -> None:
        w = Webhook.from_dict(
            {
                "id": "wh1",
                "url": "https://example.com/hook",
                "events": ["post_created"],
                "is_active": True,
                "failure_count": 0,
            }
        )
        assert w.events == ["post_created"]
        assert w.is_active is True


class TestPollResults:
    def test_from_dict(self) -> None:
        p = PollResults.from_dict(
            {
                "post_id": "p1",
                "total_votes": 42,
                "is_closed": False,
                "options": [{"id": "opt1", "text": "Yes", "votes": 30}],
            }
        )
        assert p.total_votes == 42
        assert len(p.options) == 1


class TestRateLimitInfo:
    def test_from_headers(self) -> None:
        info = RateLimitInfo.from_headers(
            {
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "95",
                "X-RateLimit-Reset": "1700000000",
            }
        )
        assert info.limit == 100
        assert info.remaining == 95
        assert info.reset == 1700000000

    def test_from_headers_lowercase(self) -> None:
        info = RateLimitInfo.from_headers(
            {
                "x-ratelimit-limit": "50",
                "x-ratelimit-remaining": "49",
            }
        )
        assert info.limit == 50
        assert info.remaining == 49

    def test_from_empty_headers(self) -> None:
        info = RateLimitInfo.from_headers({})
        assert info.limit is None
        assert info.remaining is None
        assert info.reset is None

    def test_non_numeric_ignored(self) -> None:
        info = RateLimitInfo.from_headers({"X-RateLimit-Limit": "abc"})
        assert info.limit is None
