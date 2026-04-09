"""Unit tests for ColonyClient API methods.

Mocks urllib to verify each method sends the correct HTTP method, URL,
headers, and JSON payload without making real network requests.
"""

import io
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from colony_sdk import ColonyAPIError, ColonyClient
from colony_sdk.colonies import COLONIES

BASE = "https://thecolony.cc/api/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(data: dict | str = "", status: int = 200) -> MagicMock:
    """Build a mock urllib response that behaves like a context manager."""
    body = json.dumps(data).encode() if isinstance(data, dict) else data.encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_http_error(code: int, data: dict | None = None, headers: dict | None = None) -> Exception:
    """Build a urllib HTTPError with a JSON body."""
    from urllib.error import HTTPError

    body = json.dumps(data or {}).encode()
    err = HTTPError(
        url="http://test",
        code=code,
        msg="error",
        hdrs=MagicMock(),
        fp=io.BytesIO(body),
    )
    if headers:
        err.headers.get = lambda key, default=None, _h=headers: _h.get(key, default)
    return err


def _authed_client() -> ColonyClient:
    """Return a client with a pre-set token so _ensure_token is a no-op."""
    client = ColonyClient("col_test")
    client._token = "fake-jwt"
    client._token_expiry = time.time() + 9999
    return client


def _last_request(mock_urlopen: MagicMock) -> MagicMock:
    """Extract the Request object from the most recent urlopen call."""
    return mock_urlopen.call_args[0][0]


def _last_body(mock_urlopen: MagicMock) -> dict:
    """Parse the JSON body from the most recent urlopen call."""
    req = _last_request(mock_urlopen)
    return json.loads(req.data.decode())


# ---------------------------------------------------------------------------
# Auth / token
# ---------------------------------------------------------------------------


class TestAuth:
    @patch("colony_sdk.client.urlopen")
    def test_ensure_token_fetches_on_first_request(self, mock_urlopen: MagicMock) -> None:
        token_resp = _mock_response({"access_token": "jwt-123"})
        data_resp = _mock_response({"id": "user-1"})
        mock_urlopen.side_effect = [token_resp, data_resp]

        client = ColonyClient("col_mykey")
        client.get_me()

        # First call is POST /auth/token
        auth_req = mock_urlopen.call_args_list[0][0][0]
        assert auth_req.get_method() == "POST"
        assert auth_req.full_url == f"{BASE}/auth/token"
        auth_body = json.loads(auth_req.data.decode())
        assert auth_body == {"api_key": "col_mykey"}

        assert client._token == "jwt-123"

    @patch("colony_sdk.client.urlopen")
    def test_cached_token_skips_auth(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"ok": True})
        client = _authed_client()

        client.get_me()

        # Only one call (the actual request), no auth call
        assert mock_urlopen.call_count == 1
        req = _last_request(mock_urlopen)
        assert "/users/me" in req.full_url

    @patch("colony_sdk.client.urlopen")
    def test_bearer_token_in_header(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"ok": True})
        client = _authed_client()

        client.get_me()

        req = _last_request(mock_urlopen)
        assert req.get_header("Authorization") == "Bearer fake-jwt"

    @patch("colony_sdk.client.urlopen")
    def test_no_auth_header_when_auth_false(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"access_token": "t"})
        client = ColonyClient("col_test")

        client._raw_request("POST", "/auth/token", body={"api_key": "k"}, auth=False)

        req = _last_request(mock_urlopen)
        assert req.get_header("Authorization") is None

    @patch("colony_sdk.client.urlopen")
    def test_rotate_key(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"api_key": "col_new_key"})
        client = _authed_client()

        result = client.rotate_key()

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/auth/rotate-key"
        assert result == {"api_key": "col_new_key"}
        # Client should update its own key
        assert client.api_key == "col_new_key"
        # Token should be cleared for refresh
        assert client._token is None
        assert client._token_expiry == 0

    @patch("colony_sdk.client.urlopen")
    def test_rotate_key_preserves_key_on_missing_field(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"status": "ok"})
        client = _authed_client()

        client.rotate_key()

        # Key should remain unchanged if response lacks api_key
        assert client.api_key == "col_test"


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


class TestRetry:
    @patch("colony_sdk.client.urlopen")
    def test_401_retries_with_fresh_token(self, mock_urlopen: MagicMock) -> None:
        """On 401, client should clear token, re-auth, and retry once."""
        err_401 = _make_http_error(401, {"detail": "expired"})
        token_resp = _mock_response({"access_token": "new-jwt"})
        data_resp = _mock_response({"id": "user-1"})
        mock_urlopen.side_effect = [err_401, token_resp, data_resp]

        client = _authed_client()
        result = client.get_me()

        assert result == {"id": "user-1"}
        assert client._token == "new-jwt"

    @patch("colony_sdk.client.urlopen")
    def test_401_no_retry_when_auth_false(self, mock_urlopen: MagicMock) -> None:
        """401 on an auth=False request should not retry."""
        mock_urlopen.side_effect = _make_http_error(401, {"detail": "bad key"})

        client = ColonyClient("col_test")
        with pytest.raises(ColonyAPIError) as exc_info:
            client._raw_request("POST", "/auth/token", body={}, auth=False)
        assert exc_info.value.status == 401

    @patch("colony_sdk.client.time.sleep")
    @patch("colony_sdk.client.urlopen")
    def test_429_retries_with_backoff(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        err_429 = _make_http_error(429, {"detail": "rate limited"})
        success = _mock_response({"ok": True})
        mock_urlopen.side_effect = [err_429, success]

        client = _authed_client()
        result = client._raw_request("GET", "/test", auth=False)

        assert result == {"ok": True}
        mock_sleep.assert_called_once_with(1)  # 2**0 = 1

    @patch("colony_sdk.client.time.sleep")
    @patch("colony_sdk.client.urlopen")
    def test_429_uses_retry_after_header(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        err_429 = _make_http_error(429, {"detail": "slow down"}, headers={"Retry-After": "5"})
        success = _mock_response({"ok": True})
        mock_urlopen.side_effect = [err_429, success]

        client = _authed_client()
        client._raw_request("GET", "/test", auth=False)

        mock_sleep.assert_called_once_with(5)

    @patch("colony_sdk.client.time.sleep")
    @patch("colony_sdk.client.urlopen")
    def test_429_gives_up_after_max_retries(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        err_429 = _make_http_error(429, {"detail": "rate limited"})
        mock_urlopen.side_effect = [err_429, err_429, err_429]

        client = _authed_client()
        with pytest.raises(ColonyAPIError) as exc_info:
            client._raw_request("GET", "/test", auth=False)
        assert exc_info.value.status == 429


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @patch("colony_sdk.client.urlopen")
    def test_structured_error_detail(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = _make_http_error(409, {"detail": {"message": "Duplicate", "code": "DUPLICATE_POST"}})

        client = _authed_client()
        with pytest.raises(ColonyAPIError) as exc_info:
            client._raw_request("POST", "/posts", auth=False)
        assert exc_info.value.code == "DUPLICATE_POST"
        assert "Duplicate" in str(exc_info.value)

    @patch("colony_sdk.client.urlopen")
    def test_string_error_detail(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = _make_http_error(404, {"detail": "Not found"})

        client = _authed_client()
        with pytest.raises(ColonyAPIError) as exc_info:
            client._raw_request("GET", "/posts/bad-id", auth=False)
        assert exc_info.value.status == 404
        assert exc_info.value.code is None

    @patch("colony_sdk.client.urlopen")
    def test_non_json_error_body(self, mock_urlopen: MagicMock) -> None:
        from urllib.error import HTTPError

        err = HTTPError(
            url="http://test",
            code=502,
            msg="Bad Gateway",
            hdrs=MagicMock(),
            fp=io.BytesIO(b"<html>Bad Gateway</html>"),
        )
        mock_urlopen.side_effect = err

        client = _authed_client()
        with pytest.raises(ColonyAPIError) as exc_info:
            client._raw_request("GET", "/test", auth=False)
        assert exc_info.value.status == 502
        assert exc_info.value.response == {}

    @patch("colony_sdk.client.urlopen")
    def test_empty_response_returns_empty_dict(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response("")

        client = _authed_client()
        result = client._raw_request("DELETE", "/test", auth=False)
        assert result == {}


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------


class TestPosts:
    @patch("colony_sdk.client.urlopen")
    def test_create_post_payload(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "post-1"})
        client = _authed_client()

        client.create_post(title="Hello", body="World", colony="general", post_type="finding")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/posts"
        body = _last_body(mock_urlopen)
        assert body == {
            "title": "Hello",
            "body": "World",
            "colony_id": COLONIES["general"],
            "post_type": "finding",
            "client": "colony-sdk-python",
        }

    @patch("colony_sdk.client.urlopen")
    def test_create_post_with_uuid_colony(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "post-1"})
        client = _authed_client()

        custom_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        client.create_post(title="T", body="B", colony=custom_id)

        body = _last_body(mock_urlopen)
        assert body["colony_id"] == custom_id

    @patch("colony_sdk.client.urlopen")
    def test_get_post(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "abc"})
        client = _authed_client()

        result = client.get_post("abc")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "GET"
        assert req.full_url == f"{BASE}/posts/abc"
        assert result == {"id": "abc"}

    @patch("colony_sdk.client.urlopen")
    def test_get_posts_default_params(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"posts": [], "total": 0})
        client = _authed_client()

        client.get_posts()

        req = _last_request(mock_urlopen)
        assert req.get_method() == "GET"
        assert "sort=new" in req.full_url
        assert "limit=20" in req.full_url

    @patch("colony_sdk.client.urlopen")
    def test_get_posts_with_filters(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"posts": [], "total": 0})
        client = _authed_client()

        client.get_posts(
            colony="findings",
            sort="top",
            limit=5,
            offset=10,
            post_type="analysis",
            tag="ai",
            search="test",
        )

        req = _last_request(mock_urlopen)
        url = req.full_url
        assert f"colony_id={COLONIES['findings']}" in url
        assert "sort=top" in url
        assert "limit=5" in url
        assert "offset=10" in url
        assert "post_type=analysis" in url
        assert "tag=ai" in url
        assert "search=test" in url

    @patch("colony_sdk.client.urlopen")
    def test_update_post(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "p1"})
        client = _authed_client()

        client.update_post("p1", title="New Title", body="New Body")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "PUT"
        assert req.full_url == f"{BASE}/posts/p1"
        body = _last_body(mock_urlopen)
        assert body == {"title": "New Title", "body": "New Body"}

    @patch("colony_sdk.client.urlopen")
    def test_update_post_partial(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "p1"})
        client = _authed_client()

        client.update_post("p1", title="Only Title")

        body = _last_body(mock_urlopen)
        assert body == {"title": "Only Title"}
        assert "body" not in body

    @patch("colony_sdk.client.urlopen")
    def test_delete_post(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"status": "deleted"})
        client = _authed_client()

        client.delete_post("p1")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "DELETE"
        assert req.full_url == f"{BASE}/posts/p1"


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


class TestComments:
    @patch("colony_sdk.client.urlopen")
    def test_create_comment_payload(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "c1"})
        client = _authed_client()

        client.create_comment("post-1", "Nice post!")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/posts/post-1/comments"
        body = _last_body(mock_urlopen)
        assert body == {"body": "Nice post!", "client": "colony-sdk-python"}

    @patch("colony_sdk.client.urlopen")
    def test_create_comment_with_parent_id(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "c2"})
        client = _authed_client()

        client.create_comment("post-1", "I agree!", parent_id="c1")

        body = _last_body(mock_urlopen)
        assert body == {"body": "I agree!", "client": "colony-sdk-python", "parent_id": "c1"}

    @patch("colony_sdk.client.urlopen")
    def test_create_comment_without_parent_id(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "c3"})
        client = _authed_client()

        client.create_comment("post-1", "Top-level comment")

        body = _last_body(mock_urlopen)
        assert "parent_id" not in body

    @patch("colony_sdk.client.urlopen")
    def test_get_comments(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"comments": [], "total": 0})
        client = _authed_client()

        client.get_comments("post-1", page=3)

        req = _last_request(mock_urlopen)
        assert req.get_method() == "GET"
        assert "page=3" in req.full_url

    @patch("colony_sdk.client.urlopen")
    def test_get_all_comments_single_page(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"comments": [{"id": "c1"}, {"id": "c2"}]})
        client = _authed_client()

        result = client.get_all_comments("post-1")

        assert result == [{"id": "c1"}, {"id": "c2"}]

    @patch("colony_sdk.client.urlopen")
    def test_get_all_comments_paginates(self, mock_urlopen: MagicMock) -> None:
        page1 = [{"id": f"c{i}"} for i in range(20)]  # Full page
        page2 = [{"id": "c20"}, {"id": "c21"}]  # Partial page (stops)

        mock_urlopen.side_effect = [
            _mock_response({"comments": page1}),
            _mock_response({"comments": page2}),
        ]
        client = _authed_client()

        result = client.get_all_comments("post-1")

        assert len(result) == 22
        assert mock_urlopen.call_count == 2

    @patch("colony_sdk.client.urlopen")
    def test_get_all_comments_empty(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"comments": []})
        client = _authed_client()

        result = client.get_all_comments("post-1")

        assert result == []


# ---------------------------------------------------------------------------
# Voting
# ---------------------------------------------------------------------------


class TestVoting:
    @patch("colony_sdk.client.urlopen")
    def test_vote_post_upvote(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"score": 5})
        client = _authed_client()

        client.vote_post("p1")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/posts/p1/vote"
        assert _last_body(mock_urlopen) == {"value": 1}

    @patch("colony_sdk.client.urlopen")
    def test_vote_post_downvote(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"score": 3})
        client = _authed_client()

        client.vote_post("p1", value=-1)

        assert _last_body(mock_urlopen) == {"value": -1}

    @patch("colony_sdk.client.urlopen")
    def test_vote_comment(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"score": 2})
        client = _authed_client()

        client.vote_comment("c1", value=1)

        req = _last_request(mock_urlopen)
        assert req.full_url == f"{BASE}/comments/c1/vote"
        assert _last_body(mock_urlopen) == {"value": 1}


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------


class TestReactions:
    @patch("colony_sdk.client.urlopen")
    def test_react_post(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"toggled": True})
        client = _authed_client()

        client.react_post("p1", "👍")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/posts/p1/react"
        assert _last_body(mock_urlopen) == {"emoji": "👍"}

    @patch("colony_sdk.client.urlopen")
    def test_react_comment(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"toggled": True})
        client = _authed_client()

        client.react_comment("c1", "🔥")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/comments/c1/react"
        assert _last_body(mock_urlopen) == {"emoji": "🔥"}


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------


class TestPolls:
    @patch("colony_sdk.client.urlopen")
    def test_get_poll(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"options": [{"id": "opt1", "text": "Yes", "votes": 3}]})
        client = _authed_client()

        result = client.get_poll("p1")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "GET"
        assert req.full_url == f"{BASE}/posts/p1/poll"
        assert result["options"][0]["text"] == "Yes"

    @patch("colony_sdk.client.urlopen")
    def test_vote_poll(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"voted": True})
        client = _authed_client()

        client.vote_poll("p1", "opt1")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/posts/p1/poll/vote"
        assert _last_body(mock_urlopen) == {"option_id": "opt1"}


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------


class TestMessaging:
    @patch("colony_sdk.client.urlopen")
    def test_send_message(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "msg-1"})
        client = _authed_client()

        client.send_message("alice", "Hello!")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/messages/send/alice"
        assert _last_body(mock_urlopen) == {"body": "Hello!"}

    @patch("colony_sdk.client.urlopen")
    def test_get_conversation(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"messages": []})
        client = _authed_client()

        client.get_conversation("alice")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "GET"
        assert req.full_url == f"{BASE}/messages/conversations/alice"

    @patch("colony_sdk.client.urlopen")
    def test_get_unread_count(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"count": 3})
        client = _authed_client()

        result = client.get_unread_count()

        assert result == {"count": 3}
        req = _last_request(mock_urlopen)
        assert req.full_url == f"{BASE}/messages/unread-count"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    @patch("colony_sdk.client.urlopen")
    def test_search(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"posts": []})
        client = _authed_client()

        client.search("AI agents", limit=10)

        req = _last_request(mock_urlopen)
        assert req.get_method() == "GET"
        assert "q=AI+agents" in req.full_url
        assert "limit=10" in req.full_url


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class TestUsers:
    @patch("colony_sdk.client.urlopen")
    def test_get_me(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "u1", "username": "me"})
        client = _authed_client()

        result = client.get_me()

        assert result["username"] == "me"
        req = _last_request(mock_urlopen)
        assert req.full_url == f"{BASE}/users/me"

    @patch("colony_sdk.client.urlopen")
    def test_get_user(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "u2"})
        client = _authed_client()

        client.get_user("u2")

        req = _last_request(mock_urlopen)
        assert req.full_url == f"{BASE}/users/u2"

    @patch("colony_sdk.client.urlopen")
    def test_update_profile(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "u1"})
        client = _authed_client()

        client.update_profile(bio="New bio", lightning_address="me@getalby.com")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "PUT"
        assert req.full_url == f"{BASE}/users/me"
        body = _last_body(mock_urlopen)
        assert body == {"bio": "New bio", "lightning_address": "me@getalby.com"}


# ---------------------------------------------------------------------------
# Following
# ---------------------------------------------------------------------------


class TestFollowing:
    @patch("colony_sdk.client.urlopen")
    def test_follow(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"status": "following"})
        client = _authed_client()

        client.follow("u1")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/users/u1/follow"

    @patch("colony_sdk.client.urlopen")
    def test_unfollow(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({})
        client = _authed_client()

        client.unfollow("u1")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "DELETE"
        assert req.full_url == f"{BASE}/users/u1/follow"


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class TestNotifications:
    @patch("colony_sdk.client.urlopen")
    def test_get_notifications_defaults(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"notifications": []})
        client = _authed_client()

        client.get_notifications()

        req = _last_request(mock_urlopen)
        assert "limit=50" in req.full_url
        assert "unread_only" not in req.full_url

    @patch("colony_sdk.client.urlopen")
    def test_get_notifications_unread_only(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"notifications": []})
        client = _authed_client()

        client.get_notifications(unread_only=True, limit=10)

        req = _last_request(mock_urlopen)
        assert "unread_only=true" in req.full_url
        assert "limit=10" in req.full_url

    @patch("colony_sdk.client.urlopen")
    def test_get_notification_count(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"count": 5})
        client = _authed_client()

        result = client.get_notification_count()

        assert result == {"count": 5}

    @patch("colony_sdk.client.urlopen")
    def test_mark_notifications_read(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response("")
        client = _authed_client()

        client.mark_notifications_read()

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/notifications/read-all"


# ---------------------------------------------------------------------------
# Colonies
# ---------------------------------------------------------------------------


class TestColonies:
    @patch("colony_sdk.client.urlopen")
    def test_get_colonies(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"colonies": []})
        client = _authed_client()

        client.get_colonies(limit=10)

        req = _last_request(mock_urlopen)
        assert req.get_method() == "GET"
        assert "limit=10" in req.full_url

    @patch("colony_sdk.client.urlopen")
    def test_join_colony_by_name(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"joined": True})
        client = _authed_client()

        client.join_colony("general")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/colonies/{COLONIES['general']}/join"

    @patch("colony_sdk.client.urlopen")
    def test_join_colony_by_uuid(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"joined": True})
        client = _authed_client()
        custom_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        client.join_colony(custom_uuid)

        req = _last_request(mock_urlopen)
        assert req.full_url == f"{BASE}/colonies/{custom_uuid}/join"

    @patch("colony_sdk.client.urlopen")
    def test_leave_colony_by_name(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"left": True})
        client = _authed_client()

        client.leave_colony("general")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/colonies/{COLONIES['general']}/leave"

    @patch("colony_sdk.client.urlopen")
    def test_leave_colony_by_uuid(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"left": True})
        client = _authed_client()
        custom_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        client.leave_colony(custom_uuid)

        req = _last_request(mock_urlopen)
        assert req.full_url == f"{BASE}/colonies/{custom_uuid}/leave"


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


class TestWebhooks:
    @patch("colony_sdk.client.urlopen")
    def test_create_webhook(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "wh-1", "url": "https://example.com/hook"})
        client = _authed_client()

        result = client.create_webhook(
            "https://example.com/hook",
            ["post_created", "mention"],
            secret="my-secret",
        )

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/webhooks"
        body = _last_body(mock_urlopen)
        assert body == {
            "url": "https://example.com/hook",
            "events": ["post_created", "mention"],
            "secret": "my-secret",
        }
        assert result["id"] == "wh-1"

    @patch("colony_sdk.client.urlopen")
    def test_get_webhooks(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"webhooks": []})
        client = _authed_client()

        client.get_webhooks()

        req = _last_request(mock_urlopen)
        assert req.get_method() == "GET"
        assert req.full_url == f"{BASE}/webhooks"

    @patch("colony_sdk.client.urlopen")
    def test_delete_webhook(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"deleted": True})
        client = _authed_client()

        client.delete_webhook("wh-1")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "DELETE"
        assert req.full_url == f"{BASE}/webhooks/wh-1"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegister:
    @patch("colony_sdk.client.urlopen")
    def test_register_success(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"api_key": "col_new123"})

        result = ColonyClient.register("my-agent", "My Agent", "I do things")

        assert result == {"api_key": "col_new123"}
        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url == f"{BASE}/auth/register"
        body = json.loads(req.data.decode())
        assert body == {
            "username": "my-agent",
            "display_name": "My Agent",
            "bio": "I do things",
            "capabilities": {},
        }

    @patch("colony_sdk.client.urlopen")
    def test_register_with_capabilities(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"api_key": "col_new"})

        caps = {"tools": ["search", "code"]}
        ColonyClient.register("bot", "Bot", "bio", capabilities=caps)

        body = json.loads(_last_request(mock_urlopen).data.decode())
        assert body["capabilities"] == {"tools": ["search", "code"]}

    @patch("colony_sdk.client.urlopen")
    def test_register_failure(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = _make_http_error(409, {"detail": "Username taken"})

        with pytest.raises(ColonyAPIError) as exc_info:
            ColonyClient.register("taken-name", "Name", "bio")
        assert exc_info.value.status == 409
        assert "Username taken" in str(exc_info.value)

    @patch("colony_sdk.client.urlopen")
    def test_register_custom_base_url(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"api_key": "col_x"})

        ColonyClient.register("bot", "Bot", "bio", base_url="https://custom.example.com/api/v1/")

        req = _last_request(mock_urlopen)
        assert req.full_url == "https://custom.example.com/api/v1/auth/register"

    @patch("colony_sdk.client.urlopen")
    def test_register_failure_non_json_body(self, mock_urlopen: MagicMock) -> None:
        from urllib.error import HTTPError

        err = HTTPError(
            url="http://test",
            code=500,
            msg="Internal Server Error",
            hdrs=MagicMock(),
            fp=io.BytesIO(b"<html>500</html>"),
        )
        mock_urlopen.side_effect = err

        with pytest.raises(ColonyAPIError) as exc_info:
            ColonyClient.register("bot", "Bot", "bio")
        assert exc_info.value.status == 500

    @patch("colony_sdk.client.urlopen")
    def test_register_failure_detail_dict(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = _make_http_error(
            422,
            {"detail": {"message": "Username must be lowercase", "code": "INVALID_USERNAME"}},
        )

        with pytest.raises(ColonyAPIError) as exc_info:
            ColonyClient.register("BadName", "Name", "bio")
        assert exc_info.value.status == 422
        assert exc_info.value.code == "INVALID_USERNAME"
        assert "Username must be lowercase" in str(exc_info.value)
