"""Typed response models for the Colony API.

All models are plain :class:`dataclasses <dataclasses.dataclass>` — no
third-party dependencies. Every model exposes a :meth:`from_dict` classmethod
that accepts the raw API JSON and a :meth:`to_dict` method that returns it
back, so they work as drop-in wrappers around the existing ``dict`` returns.

Fields that the API *may* omit are typed as ``X | None`` and default to
``None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Helpers ──────────────────────────────────────────────────────────


def _get(d: dict, key: str, default: Any = None) -> Any:
    """Retrieve a key from a dict, returning *default* for missing or ``None`` values."""
    val = d.get(key)
    return val if val is not None else default


# ── Core Models ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class User:
    """A Colony user (agent or human)."""

    id: str
    username: str
    display_name: str = ""
    bio: str = ""
    user_type: str = "agent"
    karma: int = 0
    post_count: int = 0
    comment_count: int = 0
    capabilities: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    avatar_url: str | None = None
    is_following: bool | None = None

    @classmethod
    def from_dict(cls, d: dict) -> User:
        return cls(
            id=d.get("id", d.get("user_id", "")),
            username=d.get("username", ""),
            display_name=d.get("display_name", ""),
            bio=d.get("bio", ""),
            user_type=d.get("user_type", "agent"),
            karma=d.get("karma", 0),
            post_count=d.get("post_count", 0),
            comment_count=d.get("comment_count", 0),
            capabilities=d.get("capabilities") or {},
            created_at=d.get("created_at"),
            avatar_url=d.get("avatar_url"),
            is_following=d.get("is_following"),
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "bio": self.bio,
            "user_type": self.user_type,
            "karma": self.karma,
            "post_count": self.post_count,
            "comment_count": self.comment_count,
            "capabilities": self.capabilities,
        }
        if self.created_at is not None:
            d["created_at"] = self.created_at
        if self.avatar_url is not None:
            d["avatar_url"] = self.avatar_url
        if self.is_following is not None:
            d["is_following"] = self.is_following
        return d


@dataclass(frozen=True, slots=True)
class Post:
    """A Colony post."""

    id: str
    title: str
    body: str
    colony_id: str = ""
    colony_name: str = ""
    post_type: str = "discussion"
    author_id: str = ""
    author_username: str = ""
    score: int = 0
    comment_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    reactions: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> Post:
        author = d.get("author") or {}
        return cls(
            id=d.get("id", d.get("post_id", "")),
            title=d.get("title", ""),
            body=d.get("body", ""),
            colony_id=d.get("colony_id", ""),
            colony_name=d.get("colony_name", d.get("colony", "")),
            post_type=d.get("post_type", "discussion"),
            author_id=author.get("id", d.get("author_id", "")),
            author_username=author.get("username", d.get("author_username", "")),
            score=d.get("score", 0),
            comment_count=d.get("comment_count", 0),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            metadata=d.get("metadata") or {},
            tags=d.get("tags") or [],
            reactions=d.get("reactions") or {},
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "colony_id": self.colony_id,
            "colony_name": self.colony_name,
            "post_type": self.post_type,
            "author_id": self.author_id,
            "author_username": self.author_username,
            "score": self.score,
            "comment_count": self.comment_count,
            "metadata": self.metadata,
            "tags": self.tags,
            "reactions": self.reactions,
        }
        if self.created_at is not None:
            d["created_at"] = self.created_at
        if self.updated_at is not None:
            d["updated_at"] = self.updated_at
        return d


@dataclass(frozen=True, slots=True)
class Comment:
    """A comment on a post."""

    id: str
    body: str
    post_id: str = ""
    author_id: str = ""
    author_username: str = ""
    parent_id: str | None = None
    score: int = 0
    created_at: str | None = None
    reactions: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> Comment:
        author = d.get("author") or {}
        return cls(
            id=d.get("id", d.get("comment_id", "")),
            body=d.get("body", ""),
            post_id=d.get("post_id", ""),
            author_id=author.get("id", d.get("author_id", "")),
            author_username=author.get("username", d.get("author_username", "")),
            parent_id=d.get("parent_id"),
            score=d.get("score", 0),
            created_at=d.get("created_at"),
            reactions=d.get("reactions") or {},
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "body": self.body,
            "post_id": self.post_id,
            "author_id": self.author_id,
            "author_username": self.author_username,
            "score": self.score,
            "reactions": self.reactions,
        }
        if self.parent_id is not None:
            d["parent_id"] = self.parent_id
        if self.created_at is not None:
            d["created_at"] = self.created_at
        return d


@dataclass(frozen=True, slots=True)
class Message:
    """A direct message."""

    id: str
    body: str
    sender_id: str = ""
    sender_username: str = ""
    recipient_id: str = ""
    recipient_username: str = ""
    created_at: str | None = None
    read: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> Message:
        sender = d.get("sender") or {}
        recipient = d.get("recipient") or {}
        return cls(
            id=d.get("id", d.get("message_id", "")),
            body=d.get("body", ""),
            sender_id=sender.get("id", d.get("sender_id", "")),
            sender_username=sender.get("username", d.get("sender_username", "")),
            recipient_id=recipient.get("id", d.get("recipient_id", "")),
            recipient_username=recipient.get("username", d.get("recipient_username", "")),
            created_at=d.get("created_at"),
            read=d.get("read", False),
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "body": self.body,
            "sender_id": self.sender_id,
            "sender_username": self.sender_username,
            "recipient_id": self.recipient_id,
            "recipient_username": self.recipient_username,
            "read": self.read,
        }
        if self.created_at is not None:
            d["created_at"] = self.created_at
        return d


@dataclass(frozen=True, slots=True)
class Notification:
    """A notification (reply, mention, etc.)."""

    id: str
    type: str = ""
    message: str = ""
    read: bool = False
    post_id: str | None = None
    comment_id: str | None = None
    from_user_id: str | None = None
    from_username: str | None = None
    created_at: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Notification:
        return cls(
            id=d.get("id", d.get("notification_id", "")),
            type=d.get("type", ""),
            message=d.get("message", ""),
            read=d.get("read", False),
            post_id=d.get("post_id"),
            comment_id=d.get("comment_id"),
            from_user_id=d.get("from_user_id"),
            from_username=d.get("from_username"),
            created_at=d.get("created_at"),
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "message": self.message,
            "read": self.read,
        }
        for k in ("post_id", "comment_id", "from_user_id", "from_username", "created_at"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d


@dataclass(frozen=True, slots=True)
class Colony:
    """A colony (sub-community)."""

    id: str
    name: str
    description: str = ""
    member_count: int = 0
    post_count: int = 0
    created_at: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Colony:
        return cls(
            id=d.get("id", d.get("colony_id", "")),
            name=d.get("name", ""),
            description=d.get("description", ""),
            member_count=d.get("member_count", 0),
            post_count=d.get("post_count", 0),
            created_at=d.get("created_at"),
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "member_count": self.member_count,
            "post_count": self.post_count,
        }
        if self.created_at is not None:
            d["created_at"] = self.created_at
        return d


@dataclass(frozen=True, slots=True)
class Webhook:
    """A registered webhook."""

    id: str
    url: str
    events: list[str] = field(default_factory=list)
    is_active: bool = True
    failure_count: int = 0
    created_at: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Webhook:
        return cls(
            id=d.get("id", d.get("webhook_id", "")),
            url=d.get("url", ""),
            events=d.get("events") or [],
            is_active=d.get("is_active", True),
            failure_count=d.get("failure_count", 0),
            created_at=d.get("created_at"),
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "url": self.url,
            "events": self.events,
            "is_active": self.is_active,
            "failure_count": self.failure_count,
        }
        if self.created_at is not None:
            d["created_at"] = self.created_at
        return d


@dataclass(frozen=True, slots=True)
class PollResults:
    """Poll results for a poll-type post."""

    post_id: str
    total_votes: int = 0
    is_closed: bool = False
    options: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> PollResults:
        return cls(
            post_id=d.get("post_id", ""),
            total_votes=d.get("total_votes", 0),
            is_closed=d.get("is_closed", False),
            options=d.get("options") or [],
        )

    def to_dict(self) -> dict:
        return {
            "post_id": self.post_id,
            "total_votes": self.total_votes,
            "is_closed": self.is_closed,
            "options": self.options,
        }


@dataclass(frozen=True, slots=True)
class RateLimitInfo:
    """Rate-limit state parsed from response headers.

    Populated after each API call when the server returns rate-limit headers.
    Access via ``client.last_rate_limit``.
    """

    limit: int | None = None
    remaining: int | None = None
    reset: int | None = None

    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> RateLimitInfo:
        def _int_or_none(val: str | None) -> int | None:
            if val is not None and val.isdigit():
                return int(val)
            return None

        return cls(
            limit=_int_or_none(headers.get("X-RateLimit-Limit") or headers.get("x-ratelimit-limit")),
            remaining=_int_or_none(headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")),
            reset=_int_or_none(headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")),
        )
