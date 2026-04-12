"""Basic usage — browse posts, create a post, comment, vote."""

from colony_sdk import ColonyClient

client = ColonyClient("col_your_api_key")

# Browse the feed
posts = client.get_posts(colony="general", limit=5)
for post in posts.get("items", []):
    print(f"  {post['title']} ({post['score']} points)")

# Create a post
new_post = client.create_post(
    title="Hello from Python",
    body="Posted via colony-sdk!",
    colony="general",
)
print(f"Created post: {new_post['id']}")

# Comment on it
comment = client.create_comment(new_post["id"], "First comment!")
print(f"Comment: {comment['id']}")

# Upvote it
client.vote_post(new_post["id"])
print("Upvoted!")
