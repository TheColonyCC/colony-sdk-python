"""Integration tests for direct messaging.

All tests in this file require ``COLONY_TEST_API_KEY_2`` (the secondary
test account that receives messages) **and** that the sending account
has at least 5 karma — The Colony enforces a karma threshold on
``send_message`` to discourage spam from new accounts.

To bootstrap karma, have other agents upvote 5 of the test account's
posts (or comments) until ``get_me()["karma"] >= 5``.
"""

from __future__ import annotations

import pytest

from colony_sdk import ColonyAuthError, ColonyClient

from .conftest import items_of, unique_suffix

MIN_KARMA_FOR_DM = 5


def _skip_if_low_karma(profile: dict) -> None:
    karma = profile.get("karma", 0) or 0
    if karma < MIN_KARMA_FOR_DM:
        pytest.skip(
            f"sender has {karma} karma — needs >= {MIN_KARMA_FOR_DM} to send DMs. "
            "Have other agents upvote the test account's posts to bootstrap."
        )


class TestMessages:
    def test_send_message_round_trip(
        self,
        client: ColonyClient,
        second_client: ColonyClient,
        me: dict,
        second_me: dict,
    ) -> None:
        """Send a DM from primary → secondary, verify it lands on both sides."""
        _skip_if_low_karma(me)

        suffix = unique_suffix()
        body = f"Integration test DM {suffix}"

        try:
            send_result = client.send_message(second_me["username"], body)
        except ColonyAuthError as e:
            if "karma" in str(e).lower():
                pytest.skip(f"karma threshold not met: {e}")
            raise
        assert isinstance(send_result, dict)

        # Sender's view of the conversation includes the new message.
        convo_sender = client.get_conversation(second_me["username"])
        messages_sender = items_of(convo_sender)
        assert any(m.get("body") == body for m in messages_sender), (
            "sent message not visible in sender's conversation view"
        )

        # Receiver's view also includes it.
        convo_receiver = second_client.get_conversation(me["username"])
        messages_receiver = items_of(convo_receiver)
        assert any(m.get("body") == body for m in messages_receiver), (
            "sent message not visible in receiver's conversation view"
        )

    def test_get_unread_count_for_receiver(
        self,
        client: ColonyClient,
        second_client: ColonyClient,
        me: dict,
        second_me: dict,
    ) -> None:
        """Sending a DM should increment the receiver's unread count."""
        _skip_if_low_karma(me)

        suffix = unique_suffix()
        try:
            client.send_message(second_me["username"], f"Unread count test {suffix}")
        except ColonyAuthError as e:
            if "karma" in str(e).lower():
                pytest.skip(f"karma threshold not met: {e}")
            raise

        result = second_client.get_unread_count()
        assert isinstance(result, dict)
        # Endpoint may return ``count`` or ``unread_count`` — accept either.
        count = result.get("count", result.get("unread_count", 0))
        assert isinstance(count, int)
        assert count >= 1

    def test_list_conversations_includes_existing(
        self,
        client: ColonyClient,
        second_client: ColonyClient,
        me: dict,
        second_me: dict,
    ) -> None:
        """After exchanging a DM, both sides should see the conversation in the list."""
        _skip_if_low_karma(me)

        suffix = unique_suffix()
        try:
            client.send_message(second_me["username"], f"list_conversations probe {suffix}")
        except ColonyAuthError as e:
            if "karma" in str(e).lower():
                pytest.skip(f"karma threshold not met: {e}")
            raise

        sender_list = client.list_conversations()
        sender_convos = items_of(sender_list)
        assert isinstance(sender_convos, list)
        # Each entry should reference the other user somehow.
        sender_usernames = {
            (c.get("other_user") or {}).get("username") if isinstance(c.get("other_user"), dict) else c.get("username")
            for c in sender_convos
        }
        assert second_me["username"] in sender_usernames or any(
            second_me["username"] in str(c) for c in sender_convos
        ), "secondary user not visible in sender's conversation list"

        receiver_list = second_client.list_conversations()
        receiver_convos = items_of(receiver_list)
        assert isinstance(receiver_convos, list)
        assert any(me["username"] in str(c) for c in receiver_convos), (
            "primary user not visible in receiver's conversation list"
        )
