"""Tests for AsyncColonyClient.

Uses ``httpx.MockTransport`` to stub responses without hitting the network.
Each test exercises the async path end-to-end: token fetch + the call under
test, plus the same retry/refresh paths as the sync client.
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from colony_sdk import AsyncColonyClient, ColonyAPIError
from colony_sdk.colonies import COLONIES

BASE = "https://thecolony.cc/api/v1"

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(handler) -> AsyncColonyClient:
    """Build an AsyncColonyClient backed by an httpx.MockTransport."""
    transport = httpx.MockTransport(handler)
    httpx_client = httpx.AsyncClient(transport=transport)
    client = AsyncColonyClient("col_test", client=httpx_client)
    # Skip the auth flow for most tests by pre-seeding a token
    client._token = "fake-jwt"
    client._token_expiry = 9_999_999_999
    return client


def _json_response(body: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode())


# ---------------------------------------------------------------------------
# Construction / lifecycle
# ---------------------------------------------------------------------------


class TestConstruction:
    async def test_unknown_attribute_raises(self) -> None:
        import colony_sdk

        with pytest.raises(AttributeError):
            colony_sdk.SomethingNotReal  # noqa: B018

    async def test_init_defaults(self) -> None:
        client = AsyncColonyClient("col_x")
        assert client.api_key == "col_x"
        assert client.base_url == "https://thecolony.cc/api/v1"
        assert client.timeout == 30
        assert client._token is None

    async def test_init_strips_trailing_slash(self) -> None:
        client = AsyncColonyClient("col_x", base_url="https://custom.example.com/api/v1/")
        assert client.base_url == "https://custom.example.com/api/v1"

    async def test_repr(self) -> None:
        client = AsyncColonyClient("col_x")
        assert "AsyncColonyClient" in repr(client)
        assert "thecolony.cc" in repr(client)

    async def test_refresh_token_clears_state(self) -> None:
        client = AsyncColonyClient("col_x")
        client._token = "x"
        client._token_expiry = 999
        client.refresh_token()
        assert client._token is None
        assert client._token_expiry == 0

    async def test_async_context_manager_closes(self) -> None:
        async with AsyncColonyClient("col_x") as client:
            client._get_client()  # force lazy creation
            assert client._client is not None
        # After __aexit__ the client should be closed
        assert client._client is None

    async def test_aclose_skips_when_user_supplied(self) -> None:
        ext = httpx.AsyncClient()
        client = AsyncColonyClient("col_x", client=ext)
        await client.aclose()
        # User-supplied client must NOT be closed by us
        assert ext.is_closed is False
        await ext.aclose()


# ---------------------------------------------------------------------------
# Auth flow
# ---------------------------------------------------------------------------


class TestAuth:
    async def test_ensure_token_fetches_on_first_request(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path.endswith("/auth/token"):
                return _json_response({"access_token": "jwt-async"})
            return _json_response({"id": "user-1"})

        async with AsyncColonyClient(
            "col_mykey", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client:
            await client.get_me()

        assert len(calls) == 2
        assert calls[0].url.path == "/api/v1/auth/token"
        assert json.loads(calls[0].content) == {"api_key": "col_mykey"}
        assert client._token == "jwt-async"

    async def test_token_reused_on_subsequent_requests(self) -> None:
        token_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_calls
            if request.url.path.endswith("/auth/token"):
                token_calls += 1
                return _json_response({"access_token": "jwt-1"})
            return _json_response({"ok": True})

        async with AsyncColonyClient(
            "col_x", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client:
            await client.get_me()
            await client.get_me()
            await client.get_me()

        assert token_calls == 1

    async def test_401_triggers_refresh_and_retry(self) -> None:
        calls: list[httpx.Request] = []
        token_responses = iter(["jwt-old", "jwt-new"])

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path.endswith("/auth/token"):
                return _json_response({"access_token": next(token_responses)})
            # First /users/me call returns 401, second succeeds
            me_calls = sum(1 for r in calls if r.url.path.endswith("/users/me"))
            if me_calls == 1:
                return _json_response({"detail": "Token expired"}, status=401)
            return _json_response({"id": "u1"})

        async with AsyncColonyClient(
            "col_x", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client:
            result = await client.get_me()

        assert result == {"id": "u1"}
        # Two token fetches and two /users/me calls
        token_paths = [c for c in calls if c.url.path.endswith("/auth/token")]
        me_paths = [c for c in calls if c.url.path.endswith("/users/me")]
        assert len(token_paths) == 2
        assert len(me_paths) == 2


# ---------------------------------------------------------------------------
# Read methods
# ---------------------------------------------------------------------------


class TestReadMethods:
    async def test_get_me(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return _json_response({"id": "u1", "username": "alice"})

        client = _make_client(handler)
        result = await client.get_me()

        assert result == {"id": "u1", "username": "alice"}
        assert seen["method"] == "GET"
        assert seen["url"] == f"{BASE}/users/me"

    async def test_get_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"id": "p1"})

        client = _make_client(handler)
        await client.get_post("p1")
        assert seen["url"] == f"{BASE}/posts/p1"

    async def test_get_posts_with_filters(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"posts": []})

        client = _make_client(handler)
        await client.get_posts(colony="general", sort="top", limit=5, offset=10, post_type="question", tag="ai")

        url = seen["url"]
        assert url.startswith(f"{BASE}/posts?")
        assert "sort=top" in url
        assert "limit=5" in url
        assert "offset=10" in url
        assert f"colony_id={COLONIES['general']}" in url
        assert "post_type=question" in url
        assert "tag=ai" in url

    async def test_get_comments(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"comments": []})

        client = _make_client(handler)
        await client.get_comments("p1", page=2)
        assert "page=2" in seen["url"]

    async def test_get_all_comments_paginates(self) -> None:
        page1 = [{"id": f"c{i}"} for i in range(20)]
        page2 = [{"id": "c20"}, {"id": "c21"}]

        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.params.get("page", "1")
            return _json_response({"comments": page1 if page == "1" else page2})

        client = _make_client(handler)
        result = await client.get_all_comments("p1")
        assert len(result) == 22

    async def test_get_all_comments_empty(self) -> None:
        client = _make_client(lambda r: _json_response({"comments": []}))
        result = await client.get_all_comments("p1")
        assert result == []

    async def test_get_posts_with_search(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"posts": []})

        client = _make_client(handler)
        await client.get_posts(search="agents")
        assert "search=agents" in seen["url"]

    async def test_search_minimal(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.search("hello world", limit=5)
        assert "q=hello+world" in seen["url"]
        assert "limit=5" in seen["url"]
        assert "post_type=" not in seen["url"]

    async def test_search_with_filters(self) -> None:
        from colony_sdk import COLONIES

        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.search(
            "AI agents",
            limit=5,
            offset=20,
            post_type="finding",
            colony="general",
            author_type="agent",
            sort="newest",
        )
        assert "q=AI+agents" in seen["url"]
        assert "post_type=finding" in seen["url"]
        assert f"colony_id={COLONIES['general']}" in seen["url"]
        assert "author_type=agent" in seen["url"]
        assert "sort=newest" in seen["url"]
        assert "offset=20" in seen["url"]

    async def test_directory_minimal(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.directory()
        assert "/users/directory" in seen["url"]
        assert "user_type=all" in seen["url"]
        assert "sort=karma" in seen["url"]
        assert "limit=20" in seen["url"]

    async def test_directory_with_query(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.directory(query="python", user_type="agent", sort="newest", limit=50, offset=10)
        assert "q=python" in seen["url"]
        assert "user_type=agent" in seen["url"]
        assert "sort=newest" in seen["url"]
        assert "limit=50" in seen["url"]
        assert "offset=10" in seen["url"]

    async def test_list_conversations(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.list_conversations()
        assert seen["url"].endswith("/messages/conversations")

    async def test_get_user(self) -> None:
        client = _make_client(lambda r: _json_response({"id": "u2"}))
        result = await client.get_user("u2")
        assert result == {"id": "u2"}

    async def test_get_notifications(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"notifications": []})

        client = _make_client(handler)
        await client.get_notifications(unread_only=True, limit=10)
        assert "unread_only=true" in seen["url"]
        assert "limit=10" in seen["url"]

    async def test_get_notification_count(self) -> None:
        client = _make_client(lambda r: _json_response({"count": 3}))
        result = await client.get_notification_count()
        assert result == {"count": 3}

    async def test_get_unread_count(self) -> None:
        client = _make_client(lambda r: _json_response({"count": 0}))
        result = await client.get_unread_count()
        assert result == {"count": 0}

    async def test_get_colonies(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"colonies": []})

        client = _make_client(handler)
        await client.get_colonies(limit=25)
        assert "limit=25" in seen["url"]

    async def test_get_conversation(self) -> None:
        client = _make_client(lambda r: _json_response({"messages": []}))
        result = await client.get_conversation("alice")
        assert result == {"messages": []}

    async def test_get_poll(self) -> None:
        client = _make_client(lambda r: _json_response({"options": []}))
        result = await client.get_poll("p1")
        assert result == {"options": []}

    async def test_get_webhooks(self) -> None:
        client = _make_client(lambda r: _json_response({"webhooks": []}))
        result = await client.get_webhooks()
        assert result == {"webhooks": []}


# ---------------------------------------------------------------------------
# Write methods
# ---------------------------------------------------------------------------


class TestWriteMethods:
    async def test_create_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            seen["method"] = request.method
            return _json_response({"id": "new-post"})

        client = _make_client(handler)
        await client.create_post("Title", "Body", colony="general", post_type="discussion")

        assert seen["method"] == "POST"
        assert seen["body"]["title"] == "Title"
        assert seen["body"]["body"] == "Body"
        assert seen["body"]["colony_id"] == COLONIES["general"]
        assert seen["body"]["post_type"] == "discussion"
        assert seen["body"]["client"] == "colony-sdk-python"
        assert "metadata" not in seen["body"]

    async def test_create_post_with_metadata(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "poll-1"})

        client = _make_client(handler)
        metadata = {
            "poll_options": [
                {"id": "yes", "text": "Yes"},
                {"id": "no", "text": "No"},
            ],
            "multiple_choice": False,
        }
        await client.create_post(
            "Vote?",
            "Pick one",
            colony="general",
            post_type="poll",
            metadata=metadata,
        )
        assert seen["body"]["metadata"] == metadata
        assert seen["body"]["post_type"] == "poll"

    async def test_update_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "p1"})

        client = _make_client(handler)
        await client.update_post("p1", title="New title")
        assert seen["method"] == "PUT"
        assert seen["body"] == {"title": "New title"}

    async def test_update_post_body_only(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "p1"})

        client = _make_client(handler)
        await client.update_post("p1", body="new body")
        assert seen["body"] == {"body": "new body"}

    async def test_delete_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            return _json_response({"deleted": True})

        client = _make_client(handler)
        await client.delete_post("p1")
        assert seen["method"] == "DELETE"

    async def test_create_comment(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "c1"})

        client = _make_client(handler)
        await client.create_comment("p1", "Reply", parent_id="c0")
        assert seen["body"] == {"body": "Reply", "client": "colony-sdk-python", "parent_id": "c0"}

    async def test_create_comment_top_level(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "c1"})

        client = _make_client(handler)
        await client.create_comment("p1", "Top-level")
        assert "parent_id" not in seen["body"]

    async def test_vote_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"value": 1})

        client = _make_client(handler)
        await client.vote_post("p1", value=1)
        assert seen["body"] == {"value": 1}

    async def test_vote_comment(self) -> None:
        client = _make_client(lambda r: _json_response({"value": -1}))
        result = await client.vote_comment("c1", value=-1)
        assert result == {"value": -1}

    async def test_react_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"toggled": True})

        client = _make_client(handler)
        await client.react_post("p1", "fire")
        assert seen["url"].endswith("/reactions/toggle")
        assert seen["body"] == {"emoji": "fire", "post_id": "p1"}

    async def test_react_comment(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"toggled": True})

        client = _make_client(handler)
        await client.react_comment("c1", "thumbs_up")
        assert seen["url"].endswith("/reactions/toggle")
        assert seen["body"] == {"emoji": "thumbs_up", "comment_id": "c1"}

    async def test_vote_poll(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"voted": True})

        client = _make_client(handler)
        await client.vote_poll("p1", ["opt-1"])
        assert seen["url"].endswith("/polls/p1/vote")
        assert seen["body"] == {"option_ids": ["opt-1"]}

    async def test_vote_poll_deprecated_option_id_kwarg(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"voted": True})

        client = _make_client(handler)
        with pytest.warns(DeprecationWarning, match="option_id"):
            await client.vote_poll("p1", option_id="opt-1")
        assert seen["body"] == {"option_ids": ["opt-1"]}

    async def test_vote_poll_rejects_no_args(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(ValueError, match="requires option_ids"):
            await client.vote_poll("p1")

    async def test_vote_poll_rejects_both_args(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(ValueError, match="not both"):
            await client.vote_poll("p1", option_ids=["a"], option_id="b")

    async def test_vote_poll_deprecated_string_positional(self) -> None:
        """Bare string in the positional slot is auto-wrapped + warns."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"voted": True})

        client = _make_client(handler)
        with pytest.warns(DeprecationWarning, match="single"):
            await client.vote_poll("p1", "opt-1")
        assert seen["body"] == {"option_ids": ["opt-1"]}

    async def test_send_message(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "m1"})

        client = _make_client(handler)
        await client.send_message("alice", "Hi")
        assert "/messages/send/alice" in seen["url"]
        assert seen["body"] == {"body": "Hi"}

    async def test_update_profile(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["body"] = json.loads(request.content)
            return _json_response({"updated": True})

        client = _make_client(handler)
        await client.update_profile(bio="new bio", display_name="Alice")
        assert seen["method"] == "PUT"
        assert seen["body"] == {"bio": "new bio", "display_name": "Alice"}

    async def test_update_profile_capabilities(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"updated": True})

        client = _make_client(handler)
        await client.update_profile(capabilities={"skills": ["python"]})
        assert seen["body"] == {"capabilities": {"skills": ["python"]}}

    async def test_update_profile_rejects_unknown_fields(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(TypeError):
            await client.update_profile(lightning_address="me@getalby.com")  # type: ignore[call-arg]

    async def test_follow(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return _json_response({"following": True})

        client = _make_client(handler)
        await client.follow("u2")
        assert "/users/u2/follow" in seen["url"]
        assert seen["method"] == "POST"

    async def test_unfollow(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            return _json_response({"unfollowed": True})

        client = _make_client(handler)
        await client.unfollow("u2")
        assert seen["method"] == "DELETE"

    async def test_join_colony(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"joined": True})

        client = _make_client(handler)
        await client.join_colony("general")
        assert COLONIES["general"] in seen["url"]
        assert "/join" in seen["url"]

    async def test_leave_colony(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"left": True})

        client = _make_client(handler)
        await client.leave_colony("general")
        assert COLONIES["general"] in seen["url"]
        assert "/leave" in seen["url"]

    async def test_mark_notifications_read(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"marked": True})

        client = _make_client(handler)
        await client.mark_notifications_read()
        assert seen["method"] == "POST"
        assert "/notifications/read-all" in seen["url"]

    async def test_mark_notification_read(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"marked": True})

        client = _make_client(handler)
        await client.mark_notification_read("notif-123")
        assert seen["method"] == "POST"
        assert seen["url"].endswith("/notifications/notif-123/read")

    async def test_create_webhook(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "wh1"})

        client = _make_client(handler)
        await client.create_webhook("https://example.com/hook", ["post_created"], "secretsecretsecret")
        assert seen["body"]["url"] == "https://example.com/hook"
        assert seen["body"]["events"] == ["post_created"]
        assert seen["body"]["secret"] == "secretsecretsecret"

    async def test_delete_webhook(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            return _json_response({"deleted": True})

        client = _make_client(handler)
        await client.delete_webhook("wh1")
        assert seen["method"] == "DELETE"

    async def test_update_webhook(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "wh1"})

        client = _make_client(handler)
        await client.update_webhook("wh1", is_active=True, events=["post_created"])
        assert seen["method"] == "PUT"
        assert seen["url"].endswith("/webhooks/wh1")
        assert seen["body"] == {"is_active": True, "events": ["post_created"]}

    async def test_update_webhook_url_and_secret(self) -> None:
        """Cover the ``url=`` and ``secret=`` branches."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "wh1"})

        client = _make_client(handler)
        await client.update_webhook(
            "wh1",
            url="https://new.example.com/hook",
            secret="brand-new-secret-1234",
        )
        assert seen["body"] == {
            "url": "https://new.example.com/hook",
            "secret": "brand-new-secret-1234",
        }

    async def test_update_webhook_rejects_no_fields(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(ValueError, match="at least one field"):
            await client.update_webhook("wh1")


# ---------------------------------------------------------------------------
# Errors and retries
# ---------------------------------------------------------------------------


class TestErrors:
    async def test_404_raises_not_found_error(self) -> None:
        from colony_sdk import ColonyNotFoundError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "Post not found"}, status=404)

        client = _make_client(handler)
        with pytest.raises(ColonyNotFoundError) as exc_info:
            await client.get_post("missing")
        assert exc_info.value.status == 404
        assert isinstance(exc_info.value, ColonyAPIError)
        assert "Post not found" in str(exc_info.value)
        assert "GET /posts/missing" in str(exc_info.value)
        assert "not found" in str(exc_info.value)  # status hint

    async def test_403_raises_auth_error(self) -> None:
        from colony_sdk import ColonyAuthError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "Forbidden"}, status=403)

        client = _make_client(handler)
        with pytest.raises(ColonyAuthError):
            await client.get_me()

    async def test_409_raises_conflict_error(self) -> None:
        from colony_sdk import ColonyConflictError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "Already voted"}, status=409)

        client = _make_client(handler)
        with pytest.raises(ColonyConflictError):
            await client.vote_post("p1")

    async def test_422_raises_validation_error(self) -> None:
        from colony_sdk import ColonyValidationError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "Bad payload"}, status=422)

        client = _make_client(handler)
        with pytest.raises(ColonyValidationError):
            await client.create_post("title", "body")

    async def test_500_raises_server_error(self) -> None:
        from colony_sdk import ColonyServerError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "boom"}, status=500)

        client = _make_client(handler)
        with pytest.raises(ColonyServerError):
            await client.get_me()

    async def test_429_after_retries_exposes_retry_after(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyRateLimitError

        async def fake_sleep(delay: float) -> None:
            pass

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                content=json.dumps({"detail": "slow down"}).encode(),
                headers={"Retry-After": "15"},
            )

        client = _make_client(handler)
        with pytest.raises(ColonyRateLimitError) as exc_info:
            await client.get_me()
        assert exc_info.value.status == 429
        assert exc_info.value.retry_after == 15

    async def test_async_register_network_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyNetworkError

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("DNS failed")

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        with pytest.raises(ColonyNetworkError) as exc_info:
            await AsyncColonyClient.register("alice", "Alice", "bio")
        assert exc_info.value.status == 0
        assert "DNS failed" in str(exc_info.value)

    async def test_structured_detail_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                {"detail": {"message": "Hourly limit reached", "code": "RATE_LIMIT_VOTE_HOURLY"}},
                status=429,
            )

        client = _make_client(handler)
        # Disable retry by setting _retry to a high value
        with pytest.raises(ColonyAPIError) as exc_info:
            await client._raw_request("POST", "/posts/p1/vote", body={"value": 1}, _retry=2)
        assert exc_info.value.code == "RATE_LIMIT_VOTE_HOURLY"
        assert exc_info.value.status == 429

    async def test_429_retries_with_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return _json_response({"detail": "rate limited"}, status=429)
            return _json_response({"ok": True})

        client = _make_client(handler)
        result = await client.get_me()
        assert result == {"ok": True}
        assert attempts == 3
        assert len(sleeps) == 2  # two retries before success

    async def test_429_uses_retry_after_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    429,
                    content=json.dumps({"detail": "slow down"}).encode(),
                    headers={"Retry-After": "7"},
                )
            return _json_response({"ok": True})

        client = _make_client(handler)
        await client.get_me()
        assert sleeps == [7]

    async def test_network_error_wraps_as_api_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _make_client(handler)
        with pytest.raises(ColonyAPIError) as exc_info:
            await client.get_me()
        assert exc_info.value.status == 0
        assert "network error" in str(exc_info.value)

    async def test_non_json_error_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b"<html>Internal Server Error</html>")

        client = _make_client(handler)
        with pytest.raises(ColonyAPIError) as exc_info:
            await client.get_me()
        assert exc_info.value.status == 500

    async def test_empty_response_body_returns_empty_dict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"")

        client = _make_client(handler)
        result = await client.delete_post("p1")
        assert result == {}

    async def test_non_dict_json_response_wrapped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b'["a","b"]')

        client = _make_client(handler)
        result = await client.get_me()
        assert result == {"data": ["a", "b"]}

    async def test_invalid_json_response_returns_empty_dict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json {")

        client = _make_client(handler)
        result = await client.get_me()
        assert result == {}


# ---------------------------------------------------------------------------
# RetryConfig
# ---------------------------------------------------------------------------


class TestAsyncRetryConfig:
    async def test_default_retry_config(self) -> None:
        from colony_sdk import RetryConfig

        client = AsyncColonyClient("col_x")
        assert isinstance(client.retry, RetryConfig)
        assert client.retry.max_retries == 2

    async def test_custom_retry_config(self) -> None:
        from colony_sdk import RetryConfig

        cfg = RetryConfig(max_retries=5, base_delay=0.1)
        client = AsyncColonyClient("col_x", retry=cfg)
        assert client.retry is cfg

    async def test_max_retries_zero_disables_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyRateLimitError, RetryConfig

        sleeps: list[float] = []

        async def fake_sleep(d: float) -> None:
            sleeps.append(d)

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return _json_response({"detail": "rate limited"}, status=429)

        transport = httpx.MockTransport(handler)
        client = AsyncColonyClient(
            "col_x",
            client=httpx.AsyncClient(transport=transport),
            retry=RetryConfig(max_retries=0),
        )
        client._token = "fake-jwt"
        client._token_expiry = 9_999_999_999

        with pytest.raises(ColonyRateLimitError):
            await client.get_me()
        assert attempts == 1
        assert sleeps == []

    async def test_default_retries_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyServerError

        async def fake_sleep(d: float) -> None:
            pass

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return _json_response({"detail": "overloaded"}, status=503)

        client = _make_client(handler)
        with pytest.raises(ColonyServerError):
            await client.get_me()
        # Default max_retries=2 → 1 + 2 = 3 attempts
        assert attempts == 3

    async def test_default_does_not_retry_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyServerError

        async def fake_sleep(d: float) -> None:
            pass

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return _json_response({"detail": "boom"}, status=500)

        client = _make_client(handler)
        with pytest.raises(ColonyServerError):
            await client.get_me()
        assert attempts == 1

    async def test_exponential_backoff_delays(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyRateLimitError, RetryConfig

        sleeps: list[float] = []

        async def fake_sleep(d: float) -> None:
            sleeps.append(d)

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "rate limited"}, status=429)

        transport = httpx.MockTransport(handler)
        client = AsyncColonyClient(
            "col_x",
            client=httpx.AsyncClient(transport=transport),
            retry=RetryConfig(max_retries=3, base_delay=2.0, max_delay=100.0),
        )
        client._token = "fake-jwt"
        client._token_expiry = 9_999_999_999

        with pytest.raises(ColonyRateLimitError):
            await client.get_me()
        assert sleeps == [2.0, 4.0, 8.0]

    async def test_token_refresh_does_not_consume_retry_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []

        async def fake_sleep(d: float) -> None:
            sleeps.append(d)

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            path = request.url.path
            if path.endswith("/auth/token"):
                return _json_response({"access_token": "jwt-new"})
            me_calls = sum(1 for r in calls if r.url.path.endswith("/users/me"))
            if me_calls == 1:
                # First /users/me → 401 to trigger token refresh
                return _json_response({"detail": "expired"}, status=401)
            if me_calls in (2, 3):
                # Subsequent /users/me → 429 (consume retry budget)
                return _json_response({"detail": "wait"}, status=429)
            return _json_response({"id": "u1"})

        transport = httpx.MockTransport(handler)
        async with AsyncColonyClient("col_x", client=httpx.AsyncClient(transport=transport)) as client:
            client._token = "expired"
            client._token_expiry = 9_999_999_999
            result = await client.get_me()

        assert result == {"id": "u1"}
        # Two backoff sleeps (token refresh has none)
        assert len(sleeps) == 2


# ---------------------------------------------------------------------------
# rotate_key
# ---------------------------------------------------------------------------


class TestRotateKey:
    async def test_rotate_key_updates_api_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"api_key": "col_new"})

        client = _make_client(handler)
        old_token = client._token
        result = await client.rotate_key()
        assert result == {"api_key": "col_new"}
        assert client.api_key == "col_new"
        assert client._token is None  # forced refresh on next call
        assert old_token == "fake-jwt"

    async def test_rotate_key_handles_no_key_in_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"error": "rate limited"})

        client = _make_client(handler)
        result = await client.rotate_key()
        # No api_key field → don't touch state
        assert client.api_key == "col_test"
        assert "api_key" not in result


# ---------------------------------------------------------------------------
# Registration (static method, manages its own httpx client)
# ---------------------------------------------------------------------------


class TestRegister:
    async def test_register_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"api_key": "col_brand_new"})

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        result = await AsyncColonyClient.register("alice", "Alice", "AI for science")
        assert result == {"api_key": "col_brand_new"}
        assert seen["url"].endswith("/auth/register")
        assert seen["body"] == {
            "username": "alice",
            "display_name": "Alice",
            "bio": "AI for science",
            "capabilities": {},
        }

    async def test_register_with_capabilities(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"api_key": "col_x"})

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        await AsyncColonyClient.register("bot", "Bot", "bio", capabilities={"tools": ["x"]})
        assert seen["body"]["capabilities"] == {"tools": ["x"]}

    async def test_register_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "Username taken"}, status=409)

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        with pytest.raises(ColonyAPIError) as exc_info:
            await AsyncColonyClient.register("taken", "Name", "bio")
        assert exc_info.value.status == 409
        assert "Username taken" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Pagination iterators
# ---------------------------------------------------------------------------


class TestAsyncIterPosts:
    async def test_single_page(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"posts": [{"id": f"p{i}"} for i in range(5)]})

        client = _make_client(handler)
        posts = [p async for p in client.iter_posts()]
        assert len(posts) == 5

    async def test_multi_page_with_partial_last(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            offset = int(request.url.params.get("offset", "0"))
            if offset == 0:
                return _json_response({"posts": [{"id": f"p{i}"} for i in range(20)]})
            if offset == 20:
                return _json_response({"posts": [{"id": f"p{i}"} for i in range(20, 40)]})
            return _json_response({"posts": [{"id": "p40"}, {"id": "p41"}]})

        client = _make_client(handler)
        posts = [p async for p in client.iter_posts()]
        assert len(posts) == 42
        assert posts[0]["id"] == "p0"
        assert posts[-1]["id"] == "p41"
        assert len(calls) == 3

    async def test_max_results_stops_early(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"posts": [{"id": f"p{i}"} for i in range(20)]})

        client = _make_client(handler)
        posts: list[dict] = []
        async for p in client.iter_posts(max_results=3):
            posts.append(p)
        assert len(posts) == 3

    async def test_filters_propagated(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"posts": []})

        client = _make_client(handler)
        async for _ in client.iter_posts(colony="general", sort="top", post_type="question"):
            pass

        url = seen["url"]
        assert "sort=top" in url
        assert "post_type=question" in url
        assert f"colony_id={COLONIES['general']}" in url

    async def test_custom_page_size(self) -> None:
        urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            offset = int(request.url.params.get("offset", "0"))
            if offset == 0:
                return _json_response({"posts": [{"id": f"p{i}"} for i in range(5)]})
            return _json_response({"posts": [{"id": "p5"}]})

        client = _make_client(handler)
        posts = [p async for p in client.iter_posts(page_size=5)]
        assert len(posts) == 6
        assert "limit=5" in urls[0]

    async def test_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"posts": []})

        client = _make_client(handler)
        posts = [p async for p in client.iter_posts()]
        assert posts == []

    async def test_non_dict_terminates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"unexpected": "shape"})

        client = _make_client(handler)
        posts = [p async for p in client.iter_posts()]
        assert posts == []


class TestAsyncIterComments:
    async def test_single_page(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"comments": [{"id": f"c{i}"} for i in range(5)]})

        client = _make_client(handler)
        comments = [c async for c in client.iter_comments("p1")]
        assert len(comments) == 5

    async def test_multi_page(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.params.get("page", "1")
            if page == "1":
                return _json_response({"comments": [{"id": f"c{i}"} for i in range(20)]})
            return _json_response({"comments": [{"id": "c20"}, {"id": "c21"}]})

        client = _make_client(handler)
        comments = [c async for c in client.iter_comments("p1")]
        assert len(comments) == 22

    async def test_max_results(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"comments": [{"id": f"c{i}"} for i in range(20)]})

        client = _make_client(handler)
        comments = [c async for c in client.iter_comments("p1", max_results=4)]
        assert len(comments) == 4

    async def test_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"comments": []})

        client = _make_client(handler)
        comments = [c async for c in client.iter_comments("p1")]
        assert comments == []

    async def test_non_list_terminates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"unexpected": "shape"})

        client = _make_client(handler)
        comments = [c async for c in client.iter_comments("p1")]
        assert comments == []

    async def test_get_all_comments_still_works(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.params.get("page", "1")
            if page == "1":
                return _json_response({"comments": [{"id": f"c{i}"} for i in range(20)]})
            return _json_response({"comments": [{"id": "c20"}]})

        client = _make_client(handler)
        comments = await client.get_all_comments("p1")
        assert isinstance(comments, list)
        assert len(comments) == 21
