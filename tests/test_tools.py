"""Tests for the agent's five tool functions — the DB-mutating core that Claude
drives. These run directly against SQLite with no API key or network. The Claude
loop itself (run_agent) builds its client lazily and is exercised manually via
claude_smoke.py. Run: pytest -q
"""
import pytest
from sqlalchemy.orm import Session

from database import Base, SessionLocal, engine
from agent import (
    tool_add_book,
    tool_delete_book,
    tool_get_book_by_id,
    tool_get_books,
    tool_update_book_status,
)


@pytest.fixture()
def db() -> Session:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_add_then_get(db):
    created = tool_add_book(db, title="Dune", author="Herbert")
    assert created["title"] == "Dune"
    assert created["status"] == "want_to_read"  # default
    fetched = tool_get_book_by_id(db, created["id"])
    assert fetched == created


def test_get_books_filters_by_status(db):
    tool_add_book(db, title="A", author="x", status="read", rating=5)
    tool_add_book(db, title="B", author="y", status="want_to_read")
    read = tool_get_books(db, status="read")
    assert [b["title"] for b in read] == ["A"]
    assert len(tool_get_books(db)) == 2


def test_get_missing_book_returns_none(db):
    assert tool_get_book_by_id(db, 9999) is None


def test_update_status_and_rating(db):
    book = tool_add_book(db, title="Dune", author="Herbert")
    updated = tool_update_book_status(db, book["id"], status="read", rating=5)
    assert updated["status"] == "read" and updated["rating"] == 5


def test_update_missing_book_returns_none(db):
    assert tool_update_book_status(db, 1234, status="read") is None


def test_delete_book(db):
    book = tool_add_book(db, title="Dune", author="Herbert")
    result = tool_delete_book(db, book["id"])
    assert result["deleted"] is True
    assert tool_get_book_by_id(db, book["id"]) is None


def test_delete_missing_book_reports_failure(db):
    result = tool_delete_book(db, 5678)
    assert result["deleted"] is False


def test_multi_step_sequence(db):
    """Mirrors the README's example: finish Dune (5 stars), then list books."""
    dune = tool_add_book(db, title="Dune", author="Herbert")
    tool_add_book(db, title="Foundation", author="Asimov", status="reading")
    tool_update_book_status(db, dune["id"], status="read", rating=5)
    books = tool_get_books(db)
    by_title = {b["title"]: b for b in books}
    assert by_title["Dune"]["status"] == "read"
    assert by_title["Dune"]["rating"] == 5
    assert len(books) == 2
