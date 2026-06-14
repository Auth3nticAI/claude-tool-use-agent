# Claude Tool-Use Agent

> A working implementation of the Anthropic tool-use loop — `run_agent(db, user_message)` runs Claude in a loop, executes the tools Claude asks for, feeds the results back as `user`-role messages, and returns when the model emits `end_turn`. Wrapped in a FastAPI endpoint that hands the model 5 real tools over a Postgres book library.

![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688?style=flat&logo=fastapi&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00?style=flat)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat&logo=postgresql&logoColor=white)
![Anthropic](https://img.shields.io/badge/Claude-Sonnet_4.6-D97757?style=flat&logo=anthropic&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white)

---

![multi-step agent response — get_books then update_book_status](screenshots/agent-multi-step.png)

## The agent loop

`agent.py` defines:

- **5 tool schemas** in the format Claude expects (`get_books`, `get_book_by_id`, `add_book`, `update_book_status`, `delete_book`).
- **5 tool functions** that talk to Postgres directly via a SQLAlchemy session — no HTTP round-trip back to our own API.
- **`run_agent(db, user_message, max_iterations=10)`** — send user message + tools → if `tool_use`, execute and feed back as user-role tool_result blocks → loop until `end_turn`. Capped at 10 iterations so a buggy description can't spin.

Each tool invocation is captured in `agent_steps` (iteration, tool, input, result) and returned with the final text so the caller can see *what happened*.

## Example multi-step request

```
POST /ai/agent
{"message": "I just finished reading Dune and want to give it 5 stars.
              Also, what am I currently reading?"}
```

The agent runs two tool calls in sequence:
1. `get_books({})` — to find Dune's id
2. `update_book_status({book_id: 2, status: "read", rating: 5})` — to mutate

Then answers both halves of the question in one final reply. See the screenshot above.

## Run

```bash
docker compose up -d db
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cat > .env <<EOF
DATABASE_URL=postgresql://postgres:password@localhost:5432/booktracker
ANTHROPIC_API_KEY=sk-ant-...
EOF

uvicorn main:app --reload
```

Try it on Swagger UI at http://localhost:8000/docs — `/ai/agent` with messages like:

- *"What's on my reading list?"* → 1 tool call (`get_books`)
- *"Add Atomic Habits by James Clear to my want-to-read list"* → 1 tool call (`add_book`)
- *"I finished 1984, mark it read with 4 stars"* → 2 calls (`get_books` → `update_book_status`)
- *"Delete the Orwell book"* → 2 calls (`get_books` → `delete_book`)

## Endpoints

This repo also includes the chat + recommendation endpoints from the earlier weeks of the same coursework:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ai/agent` | Tool-use agent loop |
| `POST` | `/ai/chat` | General book assistant |
| `POST` | `/ai/recommend` | Personalized recommendations — injects the user's library into the system prompt |
| `GET/POST/PUT/DELETE` | `/books/...` | CRUD on the book library |

## Layout

```
.
├── main.py             # FastAPI routes
├── agent.py            # Tool schemas + tool functions + run_agent loop
├── database.py         # Engine, SessionLocal, Base, get_db
├── models.py           # SQLAlchemy ORM (Book)
├── schemas.py          # Pydantic
└── requirements.txt
```

## Background

Built as the Week 6 lab for **CSE552 — Fullstack Software Development in the Age of AI Agents**. The capstone version of this app (with a frontend and Docker Compose deployment) lives at [book-tracker-ai](https://github.com/Auth3nticAI/book-tracker-ai).
