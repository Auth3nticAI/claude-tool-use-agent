"""AI agent that manages the book collection via tool use.

The agent loop:
- send user message + tool schemas to Claude
- when stop_reason == "tool_use", run each requested tool and feed results back
  as a user-role message containing tool_result blocks
- repeat until stop_reason == "end_turn" or we hit max_iterations

See lecture-notes Part 1 for the full walk-through of why this works.
Brief answers (Part 1 questions) live in the docstrings below.
"""

from pathlib import Path
from typing import Any, Callable

import anthropic
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from models import Book

load_dotenv(Path(__file__).parent / ".env")

AGENT_MODEL = "claude-sonnet-4-6"
AGENT_MAX_TOKENS = 1024

AGENT_SYSTEM_PROMPT = """You are a helpful book-tracking assistant with access \
to tools that can read, add, update, and delete books in the user's library.

Guidelines:
- Use tools to look things up before answering. Do not guess about the user's \
library contents.
- For multi-step requests, plan the order of tool calls — read before you \
update, look up an id before you delete.
- When a request is ambiguous (e.g. "delete the Orwell book"), search first, \
then act on the specific id you found. If multiple books could match, ask the \
user to clarify before deleting.
- After taking actions, confirm what you did in plain English."""

# ---------------------------------------------------------------------------
# Tool schemas — these are the contracts the model reads to decide when /
# how to call each tool. Descriptions matter: an unclear description means
# the model guesses, and that's how an agent does the wrong thing.
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_books",
        "description": (
            "List books in the user's library. Use this to look up what they "
            "already track. Optionally filter by status. Returns a list of "
            "objects with id, title, author, status, and rating. Always call "
            "this before assuming anything about the user's library."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["reading", "read", "want_to_read"],
                    "description": (
                        "Only return books with this status. Omit to return "
                        "every book."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_book_by_id",
        "description": (
            "Fetch a single book by its numeric id. Use this when you already "
            "know the id (e.g. from a previous get_books call) and need to "
            "confirm the current state before acting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "book_id": {
                    "type": "integer",
                    "description": "The book's primary key id.",
                }
            },
            "required": ["book_id"],
        },
    },
    {
        "name": "add_book",
        "description": (
            "Add a new book to the user's library. Use when the user explicitly "
            "asks to add or track a book. Returns the newly created book "
            "including its assigned id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Book title."},
                "author": {"type": "string", "description": "Author name."},
                "status": {
                    "type": "string",
                    "enum": ["reading", "read", "want_to_read"],
                    "description": (
                        "Initial status. Default to 'want_to_read' unless the "
                        "user specifies otherwise."
                    ),
                },
                "rating": {
                    "type": "integer",
                    "description": (
                        "Optional 1-5 rating. Only set when status is 'read'."
                    ),
                },
            },
            "required": ["title", "author"],
        },
    },
    {
        "name": "update_book_status",
        "description": (
            "Update an existing book's status and/or rating. Use this when the "
            "user finishes a book, starts a new one, or rates a book they have "
            "already read. You must already know the book's id — look it up "
            "with get_books first if you do not have it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "book_id": {
                    "type": "integer",
                    "description": "Id of the book to update.",
                },
                "status": {
                    "type": "string",
                    "enum": ["reading", "read", "want_to_read"],
                    "description": (
                        "New status. Omit to keep the existing status."
                    ),
                },
                "rating": {
                    "type": "integer",
                    "description": (
                        "New 1-5 rating. Only set this when status becomes "
                        "or remains 'read'."
                    ),
                },
            },
            "required": ["book_id"],
        },
    },
    {
        "name": "delete_book",
        "description": (
            "Permanently remove a book from the user's library. Use this only "
            "when the user has clearly asked to remove or delete a specific "
            "book and you have already identified its id (look it up with "
            "get_books if needed). This action cannot be undone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "book_id": {
                    "type": "integer",
                    "description": "Id of the book to delete.",
                }
            },
            "required": ["book_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool functions — the actual Python that runs when the model invokes a tool.
# Each takes the SQLAlchemy session as its first argument so we can talk to
# Postgres directly without an HTTP round-trip to our own API.
# ---------------------------------------------------------------------------


def _book_to_dict(b: Book) -> dict[str, Any]:
    return {
        "id": b.id,
        "title": b.title,
        "author": b.author,
        "status": b.status,
        "rating": b.rating,
    }


def tool_get_books(db: Session, status: str | None = None) -> list[dict]:
    query = db.query(Book)
    if status:
        query = query.filter(Book.status == status)
    return [_book_to_dict(b) for b in query.order_by(Book.id).all()]


def tool_get_book_by_id(db: Session, book_id: int) -> dict | None:
    b = db.query(Book).filter(Book.id == book_id).first()
    return _book_to_dict(b) if b else None


def tool_add_book(
    db: Session,
    title: str,
    author: str,
    status: str = "want_to_read",
    rating: int | None = None,
) -> dict:
    book = Book(title=title, author=author, status=status, rating=rating)
    db.add(book)
    db.commit()
    db.refresh(book)
    return _book_to_dict(book)


def tool_update_book_status(
    db: Session,
    book_id: int,
    status: str | None = None,
    rating: int | None = None,
) -> dict | None:
    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        return None
    if status is not None:
        book.status = status
    if rating is not None:
        book.rating = rating
    db.commit()
    db.refresh(book)
    return _book_to_dict(book)


def tool_delete_book(db: Session, book_id: int) -> dict:
    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        return {"deleted": False, "reason": "Book not found"}
    title = book.title
    db.delete(book)
    db.commit()
    return {"deleted": True, "id": book_id, "title": title}


def _build_tool_map(db: Session) -> dict[str, Callable[..., Any]]:
    """Bind the live db session to each tool function."""
    return {
        "get_books": lambda **kw: tool_get_books(db, **kw),
        "get_book_by_id": lambda **kw: tool_get_book_by_id(db, **kw),
        "add_book": lambda **kw: tool_add_book(db, **kw),
        "update_book_status": lambda **kw: tool_update_book_status(db, **kw),
        "delete_book": lambda **kw: tool_delete_book(db, **kw),
    }


# ---------------------------------------------------------------------------
# The agent loop.
# ---------------------------------------------------------------------------


def run_agent(
    db: Session,
    user_message: str,
    max_iterations: int = 10,
) -> tuple[str, list[dict]]:
    """Run the tool-use loop until the model produces a final text answer.

    Returns (final_text, agent_steps). agent_steps is a flat list of
    {tool, input, result} records, one per tool invocation.

    Part 1 — short answers:

    1) stop_reason == "tool_use": the model is asking us to run one or more
       tools before it can finish responding. The content of the assistant
       message holds one or more tool_use blocks, each with a name, input,
       and tool_use_id.

    2) tool_use_id pairs each request with the matching tool_result block we
       send back. Without it the model can't tell which result belongs to
       which call when it requested several in parallel.

    3) Tool results come back as a USER-role message because tool output is,
       to the model, new information from the outside world — same channel
       the original user prompt came in on. Putting it under "user" keeps
       the assistant/user alternation contract that the chat-completions
       protocol expects.

    4) Without max_iterations a buggy tool description or a model that
       keeps re-querying the same tool can spin forever — wasted tokens,
       wasted money, and a request that never returns. The cap is a
       circuit breaker, not part of the model's logic.
    """
    client = anthropic.Anthropic()
    tool_map = _build_tool_map(db)
    messages: list[dict] = [{"role": "user", "content": user_message}]
    steps: list[dict] = []

    for iteration in range(max_iterations):
        response = client.messages.create(
            model=AGENT_MODEL,
            max_tokens=AGENT_MAX_TOKENS,
            system=AGENT_SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            # Concatenate any text blocks in the final assistant message.
            final_text = "".join(
                block.text for block in response.content if block.type == "text"
            )
            return final_text, steps

        if response.stop_reason != "tool_use":
            # max_tokens, refusal, or some other terminal reason — return what
            # we have.
            final_text = "".join(
                block.text for block in response.content if block.type == "text"
            )
            return (
                final_text
                or f"(agent stopped with reason: {response.stop_reason})",
                steps,
            )

        # The assistant's message goes back into history verbatim so the
        # tool_use_ids line up on the next turn.
        messages.append({"role": "assistant", "content": response.content})

        tool_results: list[dict] = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = dict(block.input or {})

            fn = tool_map.get(tool_name)
            if fn is None:
                result: Any = {"error": f"Unknown tool: {tool_name}"}
            else:
                try:
                    result = fn(**tool_input)
                except Exception as exc:  # noqa: BLE001
                    result = {"error": f"{type(exc).__name__}: {exc}"}

            steps.append(
                {
                    "iteration": iteration + 1,
                    "tool": tool_name,
                    "input": tool_input,
                    "result": result,
                }
            )

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result),
                }
            )

        messages.append({"role": "user", "content": tool_results})

    # Hit the cap — return a graceful message instead of looping forever.
    return (
        "I had to stop before finishing — too many tool calls in this turn. "
        "Try breaking your request into smaller steps.",
        steps,
    )
