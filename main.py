import os
from typing import Optional

import anthropic
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from agent import run_agent
from database import Base, engine, get_db
from models import Book
from schemas import (
    AgentRequest,
    AgentResponse,
    AgentStep,
    BookCreate,
    BookResponse,
    BookUpdate,
    ChatMessage,
    ChatRequest,
    ChatResponse,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Book Tracker API", version="4.0.0 (Agent)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The Anthropic client reads ANTHROPIC_API_KEY from env automatically.
ai_client = anthropic.Anthropic()

CHAT_MODEL = "claude-sonnet-4-6"
CHAT_MAX_TOKENS = 1024

GENERAL_SYSTEM_PROMPT = """You are a helpful book assistant for a personal book \
tracking app. Help users discover books, discuss what they have read, and get \
personalized recommendations. Be conversational, enthusiastic about books, and \
concise in your responses."""


# ---------- Meta ----------


@app.get("/")
def read_root():
    return {"message": "Welcome to Book Tracker API (with AI)"}


@app.get("/health")
def health():
    return {"status": "ok", "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY"))}


# ---------- Books ----------


@app.get("/books", response_model=list[BookResponse])
def get_books(status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Book)
    if status:
        query = query.filter(Book.status == status)
    return query.all()


@app.post("/books", response_model=BookResponse, status_code=201)
def create_book(data: BookCreate, db: Session = Depends(get_db)):
    book = Book(**data.model_dump())
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


# Literal /books/stats route must come before /books/{book_id}.
@app.get("/books/stats")
def get_stats(db: Session = Depends(get_db)):
    total = db.query(Book).count()
    by_status_rows = (
        db.query(Book.status, func.count(Book.id)).group_by(Book.status).all()
    )
    by_status = {status: count for status, count in by_status_rows}
    rated_count = (
        db.query(Book)
        .filter(Book.status == "read", Book.rating.isnot(None))
        .count()
    )
    avg = (
        db.query(func.avg(Book.rating))
        .filter(Book.status == "read", Book.rating.isnot(None))
        .scalar()
    )
    average_rating = round(float(avg), 2) if avg is not None else None
    return {
        "total": total,
        "by_status": by_status,
        "average_rating": average_rating,
        "rated_count": rated_count,
    }


@app.get("/books/{book_id}", response_model=BookResponse)
def get_book(book_id: int, db: Session = Depends(get_db)):
    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


@app.put("/books/{book_id}", response_model=BookResponse)
def update_book(book_id: int, updates: BookUpdate, db: Session = Depends(get_db)):
    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    if updates.status is not None:
        book.status = updates.status
    if updates.rating is not None:
        book.rating = updates.rating
    db.commit()
    db.refresh(book)
    return book


@app.delete("/books/{book_id}")
def delete_book(book_id: int, db: Session = Depends(get_db)):
    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    title = book.title
    db.delete(book)
    db.commit()
    return {"message": f"Deleted '{title}'", "id": book_id}


# ---------- AI ----------


def _serialize_history(history: list[ChatMessage]) -> list[dict]:
    return [{"role": m.role, "content": m.content} for m in history]


def _build_book_context(db: Session) -> str:
    books = db.query(Book).all()
    read_books = [b for b in books if b.status == "read"]
    reading_books = [b for b in books if b.status == "reading"]
    want_books = [b for b in books if b.status == "want_to_read"]

    lines = ["Here is the user's book library:"]

    if read_books:
        lines.append("\nBooks they have finished:")
        for b in read_books:
            rating = f" (rated {b.rating}/5)" if b.rating else ""
            lines.append(f"- {b.title} by {b.author}{rating}")

    if reading_books:
        lines.append("\nCurrently reading:")
        for b in reading_books:
            lines.append(f"- {b.title} by {b.author}")

    if want_books:
        lines.append("\nOn their want-to-read list:")
        for b in want_books:
            lines.append(f"- {b.title} by {b.author}")

    if not (read_books or reading_books or want_books):
        lines.append("\nNo books tracked yet.")

    return "\n".join(lines)


def _call_claude(system_prompt: str, history: list[dict], user_message: str) -> str:
    """Wrap the Anthropic call so errors come back as HTTPException."""
    messages = history + [{"role": "user", "content": user_message}]
    try:
        response = ai_client.messages.create(
            model=CHAT_MODEL,
            max_tokens=CHAT_MAX_TOKENS,
            system=system_prompt,
            messages=messages,
        )
    except anthropic.APIStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Anthropic API error: {exc.message}",
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not reach Anthropic API",
        ) from exc

    return response.content[0].text


@app.post("/ai/chat", response_model=ChatResponse)
def chat_with_assistant(request: ChatRequest):
    history = _serialize_history(request.conversation_history)
    reply = _call_claude(GENERAL_SYSTEM_PROMPT, history, request.message)
    updated = history + [
        {"role": "user", "content": request.message},
        {"role": "assistant", "content": reply},
    ]
    return ChatResponse(reply=reply, updated_history=updated)


@app.post("/ai/recommend", response_model=ChatResponse)
def get_recommendations(request: ChatRequest, db: Session = Depends(get_db)):
    book_context = _build_book_context(db)

    system_prompt = (
        "You are a personalized book recommendation assistant.\n\n"
        f"{book_context}\n\n"
        "Based on this reading history, provide thoughtful, personalized "
        "recommendations. Be specific about why each recommendation matches "
        "their taste. Keep responses concise — 2-3 recommendations at most "
        "unless asked for more. When you reference a book they have already "
        "logged, anchor your suggestion to it (e.g., 'since you enjoyed X, "
        "you might like...')."
    )

    history = _serialize_history(request.conversation_history)
    reply = _call_claude(system_prompt, history, request.message)
    updated = history + [
        {"role": "user", "content": request.message},
        {"role": "assistant", "content": reply},
    ]
    return ChatResponse(reply=reply, updated_history=updated)


# ---------- Agent ----------


@app.post("/ai/agent", response_model=AgentResponse)
def book_agent(request: AgentRequest, db: Session = Depends(get_db)):
    """Run the tool-use agent loop on a single user message."""
    final_text, steps = run_agent(db, request.message)
    return AgentResponse(
        response=final_text,
        agent_steps=[AgentStep(**step) for step in steps],
    )
