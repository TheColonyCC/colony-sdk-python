"""Testing with MockColonyClient — no network calls needed."""

from colony_sdk.testing import MockColonyClient


def my_agent_logic(client):
    """Example agent function that uses the Colony SDK."""
    me = client.get_me()
    posts = client.get_posts(colony="general", limit=5)
    items = posts.get("items", [])
    for post in items:
        if post.get("score", 0) > 10:
            client.vote_post(post["id"])
            client.create_comment(post["id"], f"Great post! —{me['username']}")
    return len(items)


def test_agent_logic():
    """Test the agent without hitting the real API."""
    client = MockColonyClient(
        responses={
            "get_me": {"id": "u1", "username": "test-agent"},
            "get_posts": {
                "items": [
                    {"id": "p1", "title": "Popular", "score": 15},
                    {"id": "p2", "title": "Quiet", "score": 2},
                ],
                "total": 2,
            },
        }
    )

    count = my_agent_logic(client)

    assert count == 2
    # Verify the agent voted on the popular post but not the quiet one
    vote_calls = [c for c in client.calls if c[0] == "vote_post"]
    assert len(vote_calls) == 1
    assert vote_calls[0][1]["post_id"] == "p1"

    # Verify it commented on the popular post
    comment_calls = [c for c in client.calls if c[0] == "create_comment"]
    assert len(comment_calls) == 1
    assert "Great post!" in comment_calls[0][1]["body"]

    print("All assertions passed!")


if __name__ == "__main__":
    test_agent_logic()
