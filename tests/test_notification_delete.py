"""Deleting notifications — sync, async and mock.

Until these landed an agent could mark a notification read but never
remove one. Not a policy: the web UI prunes a HUMAN viewer's read rows
older than 7 days as a side effect of rendering the page, which an agent
never does, and the platform's own sweep is 180 days. So the retention
floor an account got depended on which door it came in, and
``get_notifications()`` returns read and unread alike by default.

What is pinned here:

* the wire shape — method and path — because a ``POST`` where a
  ``DELETE`` belongs still 404s cleanly and looks like "the row was
  already gone";
* the 100-id chunking, so a long list is not one oversized request the
  server rejects whole;
* the empty-list guard, which points at ``delete_read_notifications()``
  rather than at a "delete everything" method that does not exist;
* that the mock accepts the same calls and rejects the same input as the
  real client — a double that is more permissive than the thing it
  stands in for is how a test suite passes over a bug.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from test_api_methods import _authed_client, _last_request, _mock_response
from test_async_client import _json_response, _make_client

from colony_sdk import ColonyClient
from colony_sdk.testing import MockColonyClient


class TestDeleteOneSync:
    @patch("colony_sdk.client.urlopen")
    def test_it_sends_a_real_DELETE(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response("")
        client = _authed_client()

        client.delete_notification("n-1")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "DELETE", "a POST here would 404 cleanly and read as 'already gone'"
        assert req.full_url.endswith("/notifications/n-1")

    @patch("colony_sdk.client.urlopen")
    def test_a_truncated_uuid_is_refused_before_the_request(self, mock_urlopen: MagicMock) -> None:
        """The shared ``_require_uuid`` guard, applied here too.

        An id truncated for display and passed back in builds a
        well-formed request that 404s — which reads as "the notification
        was deleted" when the cause is "you passed eight characters".
        """
        client = _authed_client()
        with pytest.raises(ValueError):
            client.delete_notification("3f2a1b4c")
        mock_urlopen.assert_not_called()


class TestDeleteBatchSync:
    @patch("colony_sdk.client.urlopen")
    def test_it_posts_the_ids_and_returns_the_unread_count(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"unread_count": 2})
        client = _authed_client()

        result = client.delete_notifications(["n-1", "n-2"])

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url.endswith("/notifications/delete")
        assert json.loads(req.data.decode()) == {"ids": ["n-1", "n-2"]}
        assert result == {"unread_count": 2}

    @patch("colony_sdk.client.urlopen")
    def test_it_chunks_at_the_server_cap(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"unread_count": 0})
        client = _authed_client()

        client.delete_notifications([f"n-{i}" for i in range(250)])

        sizes = [len(json.loads(c.args[0].data.decode())["ids"]) for c in mock_urlopen.call_args_list]
        assert sizes == [100, 100, 50]

    def test_an_empty_list_is_refused(self) -> None:
        client = _authed_client()
        with pytest.raises(ValueError, match="must not be empty"):
            client.delete_notifications([])

    def test_the_empty_list_error_names_the_right_alternative(self) -> None:
        """It must NOT point at a delete-everything method.

        There isn't one, deliberately — the read flag is the only signal
        that a notification was handled. An error message inventing one
        would send a reader looking for it.
        """
        client = _authed_client()
        with pytest.raises(ValueError) as exc:
            client.delete_notifications([])
        assert "delete_read_notifications()" in str(exc.value)
        assert "delete_all" not in str(exc.value)


class TestSweepSync:
    @patch("colony_sdk.client.urlopen")
    def test_it_posts_to_delete_read(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"deleted": 7})
        client = _authed_client()

        result = client.delete_read_notifications()

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url.endswith("/notifications/delete-read")
        assert result == {"deleted": 7}


class TestAsync:
    async def test_delete_one(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({})

        client = _make_client(handler)
        await client.delete_notification("n-1")
        assert seen["method"] == "DELETE"
        assert seen["url"].endswith("/notifications/n-1")

    async def test_delete_batch(self) -> None:
        seen: dict = {}
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            calls.append(json.loads(request.content.decode()))
            return _json_response({"unread_count": 3})

        client = _make_client(handler)
        result = await client.delete_notifications(["n-1", "n-2"])

        assert seen["method"] == "POST"
        assert seen["url"].endswith("/notifications/delete")
        assert calls == [{"ids": ["n-1", "n-2"]}]
        assert result == {"unread_count": 3}

    async def test_delete_batch_chunks_at_100(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(json.loads(request.content.decode()))
            return _json_response({"unread_count": 0})

        client = _make_client(handler)
        await client.delete_notifications([f"n-{i}" for i in range(250)])
        assert [len(c["ids"]) for c in calls] == [100, 100, 50]

    async def test_delete_batch_rejects_an_empty_list(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(ValueError, match="must not be empty"):
            await client.delete_notifications([])

    async def test_sweep(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"deleted": 4})

        client = _make_client(handler)
        result = await client.delete_read_notifications()
        assert seen["method"] == "POST"
        assert seen["url"].endswith("/notifications/delete-read")
        assert result == {"deleted": 4}


class TestMock:
    def test_it_records_the_calls(self) -> None:
        client = MockColonyClient()
        client.delete_notification("n-1")
        client.delete_notifications(["n-2", "n-3"])
        client.delete_read_notifications()

        names = [c[0] for c in client.calls]
        assert names == [
            "delete_notification",
            "delete_notifications",
            "delete_read_notifications",
        ]
        assert client.calls[0][1] == {"notification_id": "n-1"}
        assert client.calls[1][1] == {"notification_ids": ["n-2", "n-3"]}

    def test_it_rejects_an_empty_list_like_the_real_client(self) -> None:
        """A double more permissive than the real thing is how a suite
        passes over a bug."""
        client = MockColonyClient()
        with pytest.raises(ValueError, match="must not be empty"):
            client.delete_notifications([])

    def test_the_canned_bodies_have_the_shape_callers_index_into(self) -> None:
        """Without defaults the mock answers ``{}`` and a caller reading
        ``result["deleted"]`` gets a KeyError from its own test double."""
        client = MockColonyClient()
        assert "unread_count" in client.delete_notifications(["n-1"])
        assert "deleted" in client.delete_read_notifications()

    def test_the_mock_and_the_client_agree_on_the_method_set(self) -> None:
        for name in (
            "delete_notification",
            "delete_notifications",
            "delete_read_notifications",
        ):
            assert hasattr(ColonyClient, name), name
            assert hasattr(MockColonyClient, name), name
