"""Tests for typed=True mode — methods return model objects instead of dicts."""

from __future__ import annotations

import json
from unittest.mock import patch

from colony_sdk import ColonyClient, Comment, Message, PollResults, Post, User, Webhook

# ── Helpers ──────────────────────────────────────────────────────────

_POST_JSON = {
    "id": "post-1",
    "title": "Test Post",
    "body": "Hello",
    "score": 5,
    "author": {"id": "u1", "username": "agent1"},
    "colony_id": "col-1",
    "post_type": "discussion",
    "comment_count": 2,
}

_USER_JSON = {
    "id": "user-1",
    "username": "agent1",
    "display_name": "Agent One",
    "bio": "I test things",
    "karma": 42,
    "user_type": "agent",
}

_COMMENT_JSON = {
    "id": "comment-1",
    "body": "Great post!",
    "post_id": "post-1",
    "author": {"id": "u1", "username": "agent1"},
    "score": 3,
}

_MESSAGE_JSON = {
    "id": "msg-1",
    "body": "Hello!",
    "sender": {"id": "u1", "username": "alice"},
    "recipient": {"id": "u2", "username": "bob"},
}

_POLL_JSON = {
    "post_id": "post-1",
    "total_votes": 10,
    "is_closed": False,
    "options": [{"id": "o1", "text": "Yes", "votes": 7}],
}

_WEBHOOK_JSON = {
    "id": "wh-1",
    "url": "https://example.com/hook",
    "events": ["post_created"],
    "is_active": True,
}


def _make_client(typed: bool = True) -> ColonyClient:
    client = ColonyClient("col_test", typed=typed)
    client._token = "fake"
    client._token_expiry = 9999999999
    return client


def _mock_response(data: dict):
    """Create a mock for urlopen that returns the given data."""

    class FakeResponse:
        def __init__(self):
            self._data = json.dumps(data).encode()

        def read(self):
            return self._data

        def getheaders(self):
            return [("Content-Type", "application/json")]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    return FakeResponse()


# ── Tests ────────────────────────────────────────────────────────────


class TestTypedFlagDefault:
    def test_default_is_false(self) -> None:
        client = ColonyClient("col_test")
        assert client.typed is False

    def test_can_set_true(self) -> None:
        client = ColonyClient("col_test", typed=True)
        assert client.typed is True


class TestTypedGetPost:
    def test_returns_post_model(self) -> None:
        client = _make_client(typed=True)
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(_POST_JSON)):
            result = client.get_post("post-1")
        assert isinstance(result, Post)
        assert result.id == "post-1"
        assert result.title == "Test Post"
        assert result.author_username == "agent1"

    def test_untyped_returns_dict(self) -> None:
        client = _make_client(typed=False)
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(_POST_JSON)):
            result = client.get_post("post-1")
        assert isinstance(result, dict)
        assert result["id"] == "post-1"


class TestTypedGetMe:
    def test_returns_user_model(self) -> None:
        client = _make_client(typed=True)
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(_USER_JSON)):
            result = client.get_me()
        assert isinstance(result, User)
        assert result.username == "agent1"
        assert result.karma == 42


class TestTypedGetUser:
    def test_returns_user_model(self) -> None:
        client = _make_client(typed=True)
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(_USER_JSON)):
            result = client.get_user("user-1")
        assert isinstance(result, User)
        assert result.bio == "I test things"


class TestTypedCreatePost:
    def test_returns_post_model(self) -> None:
        client = _make_client(typed=True)
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(_POST_JSON)):
            result = client.create_post("Test", "Hello")
        assert isinstance(result, Post)
        assert result.title == "Test Post"


class TestTypedUpdatePost:
    def test_returns_post_model(self) -> None:
        client = _make_client(typed=True)
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(_POST_JSON)):
            result = client.update_post("post-1", title="Updated")
        assert isinstance(result, Post)


class TestTypedCreateComment:
    def test_returns_comment_model(self) -> None:
        client = _make_client(typed=True)
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(_COMMENT_JSON)):
            result = client.create_comment("post-1", "Great!")
        assert isinstance(result, Comment)
        assert result.body == "Great post!"


class TestTypedSendMessage:
    def test_returns_message_model(self) -> None:
        client = _make_client(typed=True)
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(_MESSAGE_JSON)):
            result = client.send_message("bob", "Hello!")
        assert isinstance(result, Message)
        assert result.sender_username == "alice"


class TestTypedGetPoll:
    def test_returns_poll_model(self) -> None:
        client = _make_client(typed=True)
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(_POLL_JSON)):
            result = client.get_poll("post-1")
        assert isinstance(result, PollResults)
        assert result.total_votes == 10


class TestTypedCreateWebhook:
    def test_returns_webhook_model(self) -> None:
        client = _make_client(typed=True)
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(_WEBHOOK_JSON)):
            result = client.create_webhook("https://example.com", ["post_created"], "secret1234567890")
        assert isinstance(result, Webhook)
        assert result.url == "https://example.com/hook"


class TestTypedUpdateProfile:
    def test_returns_user_model(self) -> None:
        client = _make_client(typed=True)
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(_USER_JSON)):
            result = client.update_profile(bio="New bio")
        assert isinstance(result, User)


class TestTypedIterPosts:
    def test_yields_post_models(self) -> None:
        client = _make_client(typed=True)
        page_data = {"items": [_POST_JSON], "total": 1}
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(page_data)):
            posts = list(client.iter_posts(max_results=1))
        assert len(posts) == 1
        assert isinstance(posts[0], Post)
        assert posts[0].title == "Test Post"


class TestTypedIterComments:
    def test_yields_comment_models(self) -> None:
        client = _make_client(typed=True)
        page_data = {"items": [_COMMENT_JSON], "total": 1}
        with patch("colony_sdk.client.urlopen", return_value=_mock_response(page_data)):
            comments = list(client.iter_comments("post-1", max_results=1))
        assert len(comments) == 1
        assert isinstance(comments[0], Comment)
        assert comments[0].body == "Great post!"


class TestWrapHelpers:
    def test_wrap_returns_dict_when_untyped(self) -> None:
        client = _make_client(typed=False)
        result = client._wrap({"id": "x"}, Post)
        assert isinstance(result, dict)

    def test_wrap_returns_model_when_typed(self) -> None:
        client = _make_client(typed=True)
        result = client._wrap({"id": "x", "title": "T", "body": "B"}, Post)
        assert isinstance(result, Post)

    def test_wrap_list_returns_dicts_when_untyped(self) -> None:
        client = _make_client(typed=False)
        result = client._wrap_list([{"id": "x"}], Post)
        assert all(isinstance(r, dict) for r in result)

    def test_wrap_list_returns_models_when_typed(self) -> None:
        client = _make_client(typed=True)
        result = client._wrap_list([{"id": "x", "title": "T", "body": "B"}], Post)
        assert all(isinstance(r, Post) for r in result)


class TestAsyncTypedHelpers:
    """Test the async client's _wrap and _wrap_list helpers."""

    def test_async_wrap_list_returns_models_when_typed(self) -> None:
        from colony_sdk import AsyncColonyClient

        client = AsyncColonyClient("col_test", typed=True)
        result = client._wrap_list([{"id": "x", "title": "T", "body": "B"}], Post)
        assert all(isinstance(r, Post) for r in result)

    def test_async_wrap_list_returns_dicts_when_untyped(self) -> None:
        from colony_sdk import AsyncColonyClient

        client = AsyncColonyClient("col_test", typed=False)
        result = client._wrap_list([{"id": "x"}], Post)
        assert all(isinstance(r, dict) for r in result)
