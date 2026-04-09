"""Integration tests for direct messaging.

All tests in this file require ``COLONY_TEST_API_KEY_2`` (the secondary
test account that receives messages).
"""

from __future__ import annotations

from colony_sdk import ColonyClient

from .conftest import unique_suffix


class TestMessages:
    def test_send_message_round_trip(
        self,
        client: ColonyClient,
        second_client: ColonyClient,
        me: dict,
        second_me: dict,
    ) -> None:
        """Send a DM from primary → secondary, verify it lands on both sides."""
        suffix = unique_suffix()
        body = f"Integration test DM {suffix}"

        send_result = client.send_message(second_me["username"], body)
        assert isinstance(send_result, dict)

        # Sender's view of the conversation includes the new message.
        convo_sender = client.get_conversation(second_me["username"])
        messages_sender = convo_sender.get("messages", convo_sender) if isinstance(convo_sender, dict) else convo_sender
        assert isinstance(messages_sender, list)
        assert any(m.get("body") == body for m in messages_sender), (
            "sent message not visible in sender's conversation view"
        )

        # Receiver's view also includes it.
        convo_receiver = second_client.get_conversation(me["username"])
        messages_receiver = (
            convo_receiver.get("messages", convo_receiver) if isinstance(convo_receiver, dict) else convo_receiver
        )
        assert isinstance(messages_receiver, list)
        assert any(m.get("body") == body for m in messages_receiver), (
            "sent message not visible in receiver's conversation view"
        )

    def test_get_unread_count_for_receiver(
        self, client: ColonyClient, second_client: ColonyClient, second_me: dict
    ) -> None:
        """Sending a DM should increment the receiver's unread count."""
        suffix = unique_suffix()
        client.send_message(second_me["username"], f"Unread count test {suffix}")
        result = second_client.get_unread_count()
        assert isinstance(result, dict)
        # Endpoint may return ``count`` or ``unread_count`` — accept either.
        count = result.get("count", result.get("unread_count", 0))
        assert isinstance(count, int)
        assert count >= 1
