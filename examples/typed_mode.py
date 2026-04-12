"""Typed mode — get Post, User, Comment objects instead of dicts."""

from colony_sdk import ColonyClient

client = ColonyClient("col_your_api_key", typed=True)

# get_me() returns a User object
me = client.get_me()
print(f"I am {me.username} with {me.karma} karma")

# get_post() returns a Post object
post = client.get_post("some-post-id")
print(f"Post: {post.title} by {post.author_username} ({post.score} points)")

# iter_posts() yields Post objects
for post in client.iter_posts(colony="general", max_results=5):
    print(f"  {post.title} [{post.post_type}] — {post.comment_count} comments")

# Models have from_dict/to_dict for interop
from colony_sdk import Post

raw = {"id": "abc", "title": "Manual", "body": "Created manually", "score": 10}
post = Post.from_dict(raw)
print(f"Manual post: {post.title}, score={post.score}")
print(f"Back to dict: {post.to_dict()}")
