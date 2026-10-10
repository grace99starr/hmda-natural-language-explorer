"""
Claude (Anthropic) client used by the NL -> SQL layer.

Reads ANTHROPIC_API_KEY and (optionally) CLAUDE_MODEL from a .env file in the
project root (gitignored, never committed) via python-dotenv. Running this
file directly sends one trivial message to Claude to confirm the API key and
network path work before anything else is built on top of it.
"""

import os
import sys

import anthropic
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-4-6"
MODEL = os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)


def get_client() -> anthropic.Anthropic:
    """Build an Anthropic client from ANTHROPIC_API_KEY in .env/the environment."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not found. Create a .env file in the project "
            "root with ANTHROPIC_API_KEY=sk-ant-... (never commit it)."
        )
    return anthropic.Anthropic(api_key=api_key)


def test_connection() -> str:
    """Send a minimal message to Claude and return its reply."""
    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=20,
        messages=[{"role": "user", "content": "Reply with exactly: pong"}],
    )
    return response.content[0].text


if __name__ == "__main__":
    try:
        reply = test_connection()
    except Exception as e:
        print(f"Connection test failed: {e}")
        sys.exit(1)
    print(f"Connection OK. Claude replied: {reply!r}")
