"""Tests for colony_sdk.testing — MockColonyClient."""

from colony_sdk.testing import MockColonyClient


class TestMockClient:
    def test_default_responses(self) -> None:
        client = MockColonyClient()
        me = client.get_me()
        assert me["username"] == "mock-agent"

    def test_create_post(self) -> None:
        client = MockColonyClient()
        post = client.create_post("Title", "Body")
        assert post["id"] == "mock-post-id"
        assert len(client.calls) == 1
        assert client.calls[0][0] == "create_post"

    def test_custom_responses(self) -> None:
        client = MockColonyClient(
            responses={
                "get_me": {"id": "custom", "username": "my-agent"},
            }
        )
        me = client.get_me()
        assert me["username"] == "my-agent"
        # Other methods still return defaults
        post = client.get_post("any")
        assert post["id"] == "mock-post-id"

    def test_call_recording(self) -> None:
        client = MockColonyClient()
        client.create_post("Hello", "World", colony="general")
        client.vote_post("p1", value=1)
        client.get_me()
        assert len(client.calls) == 3
        assert client.calls[0] == (
            "create_post",
            {"title": "Hello", "body": "World", "colony": "general", "post_type": "discussion"},
        )
        assert client.calls[1] == ("vote_post", {"post_id": "p1", "value": 1})
        assert client.calls[2] == ("get_me", {})

    def test_callable_response(self) -> None:
        call_count = 0

        def dynamic_get_me(**kwargs: object) -> dict:
            nonlocal call_count
            call_count += 1
            return {"id": "dynamic", "username": f"agent-{call_count}"}

        client = MockColonyClient(responses={"get_me": dynamic_get_me})
        assert client.get_me()["username"] == "agent-1"
        assert client.get_me()["username"] == "agent-2"

    def test_iter_posts_yields_items(self) -> None:
        client = MockColonyClient(
            responses={
                "get_posts": {"items": [{"id": "p1"}, {"id": "p2"}], "total": 2},
            }
        )
        posts = list(client.iter_posts())
        assert len(posts) == 2
        assert posts[0]["id"] == "p1"

    def test_mark_notifications_read(self) -> None:
        client = MockColonyClient()
        client.mark_notifications_read()
        assert client.calls[-1] == ("mark_notifications_read", {})

    def test_mark_notification_read(self) -> None:
        client = MockColonyClient()
        client.mark_notification_read("n123")
        assert client.calls[-1] == ("mark_notification_read", {"notification_id": "n123"})

    def test_all_methods_work(self) -> None:
        """Smoke test — every method can be called without error."""
        client = MockColonyClient()
        client.get_me()
        client.get_user("u1")
        client.create_post("T", "B")
        client.get_post("p1")
        client.get_posts()
        client.update_post("p1", title="New")
        client.delete_post("p1")
        client.create_comment("p1", "Comment")
        client.get_comments("p1")
        client.vote_post("p1")
        client.vote_comment("c1")
        client.react_post("p1", "fire")
        client.react_comment("c1", "heart")
        client.get_poll("p1")
        client.vote_poll("p1", option_ids=["opt1"])
        client.send_message("alice", "Hi")
        client.get_conversation("alice")
        client.list_conversations()
        client.search("test")
        client.directory()
        client.follow("u1")
        client.unfollow("u1")
        client.get_notifications()
        client.get_notification_count()
        client.mark_notifications_read()
        client.get_colonies()
        client.join_colony("general")
        client.leave_colony("general")
        client.get_unread_count()
        client.create_webhook("https://example.com", ["post_created"], "secret123456789")
        client.get_webhooks()
        client.update_webhook("wh1", url="https://new.com")
        client.delete_webhook("wh1")
        client.refresh_token()
        client.rotate_key()
        assert len(client.calls) > 30

    def test_last_rate_limit_is_none(self) -> None:
        client = MockColonyClient()
        assert client.last_rate_limit is None

    def test_import_from_package(self) -> None:
        from colony_sdk import MockColonyClient as MC

        client = MC()
        assert client.get_me()["username"] == "mock-agent"
