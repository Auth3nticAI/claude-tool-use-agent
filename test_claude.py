"""Smoke test the Anthropic API key end-to-end.

Loads ANTHROPIC_API_KEY from .env (sibling file), calls Claude, prints reply.
"""

from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

client = anthropic.Anthropic()

message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=256,
    messages=[
        {
            "role": "user",
            "content": "What is the book '1984' about in one paragraph?",
        }
    ],
)

print(message.content[0].text)
