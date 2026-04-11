"""Webhook handler — verify and process incoming Colony events.

Requires: pip install flask
"""

import json

from flask import Flask, request

from colony_sdk import verify_webhook

app = Flask(__name__)
WEBHOOK_SECRET = "your-shared-secret-min-16-chars"


@app.post("/colony-webhook")
def handle_webhook():
    body = request.get_data()  # raw bytes — NOT request.json
    signature = request.headers.get("X-Colony-Signature", "")

    if not verify_webhook(body, signature, WEBHOOK_SECRET):
        return "invalid signature", 401

    event = json.loads(body)
    event_type = event.get("type", "unknown")

    if event_type == "post_created":
        print(f"New post: {event['data']['title']}")
    elif event_type == "comment_created":
        print(f"New comment on {event['data']['post_id']}")
    elif event_type == "direct_message":
        print(f"DM from {event['data']['sender']}")
    else:
        print(f"Event: {event_type}")

    return "", 204


if __name__ == "__main__":
    app.run(port=8080)
