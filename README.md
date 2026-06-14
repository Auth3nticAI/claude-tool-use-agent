# Book Tracker API — Week 6 (Agent)

Backend for the Book Tracker app, extended in Week 6 with an **AI agent** that can read, add, update, and delete books through natural-language requests.

Builds on the Week 5 chat endpoints (`/ai/chat`, `/ai/recommend`). The new piece is `/ai/agent`, which runs Claude in a tool-use loop against five tools that wrap the Book Tracker's SQLAlchemy layer.

## Stack

- **FastAPI** + **SQLAlchemy** + **PostgreSQL 16** (Week 4 baseline)
- **Anthropic Python SDK** for chat (Week 5)
- **Anthropic tool-use loop** for the agent (Week 6, this lab)

## The agent

`agent.py` defines:

- **5 tool schemas** (`get_books`, `get_book_by_id`, `add_book`, `update_book_status`, `delete_book`) in the format Claude expects
- **5 tool functions** that talk to Postgres directly via a SQLAlchemy session — no HTTP round-trip to our own API
- **`run_agent(db, user_message, max_iterations=10)`** — the loop:
  1. Send the user message + tool schemas to Claude
  2. If `stop_reason == "end_turn"`, return the final text
  3. If `stop_reason == "tool_use"`, run each requested tool, append the assistant's message + a user-role tool_result message, loop
  4. Cap at `max_iterations` so a buggy tool description can't spin forever

Each tool invocation is captured in `agent_steps` (iteration, tool name, input, result) and returned alongside the final text so the caller can see what happened.

## Endpoint

| Method | Path | Purpose |
|---|---|---|
| POST | `/ai/agent` | Single-turn agent. Takes `{"message": "..."}` and returns `{"response": "...", "agent_steps": [...]}` |

Plus everything from Week 5: `/ai/chat`, `/ai/recommend`, and the full CRUD layer (`/books`, `/books/{id}`, `/books/stats`).

## Example: multi-step request

```
POST /ai/agent
{"message": "I just finished reading Dune and want to give it 5 stars.
              Also, what am I currently reading?"}
```

The agent ran two tool calls:

1. `get_books({})` — fetch the library to find Dune's id
2. `update_book_status({book_id: 2, status: "read", rating: 5})` — mutate

Then answered both halves of the question in one final reply. See [screenshots/agent-multi-step.png](screenshots/agent-multi-step.png).

## Run

```bash
# 1. Start Postgres
docker compose up -d db

# 2. Install
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. .env
cat > .env <<EOF
DATABASE_URL=postgresql://postgres:password@localhost:5432/booktracker
ANTHROPIC_API_KEY=sk-ant-...
EOF

# 4. Start the API
uvicorn main:app --reload
```

Open http://localhost:8000/docs and try `/ai/agent` with messages like:

- *"What's on my reading list?"*
- *"Add Atomic Habits by James Clear to my want-to-read list"*
- *"I finished 1984, mark it as read with 4 stars"*
- *"Delete the book about George Orwell"*

## Code organization

```
.
├── main.py                  # FastAPI routes (books + chat + recommend + agent)
├── agent.py                 # NEW: tool schemas, tool functions, run_agent loop
├── database.py              # Engine, SessionLocal, Base, get_db
├── models.py                # SQLAlchemy ORM (Book)
├── schemas.py               # Pydantic — Book*, Chat*, NEW: AgentRequest/Step/Response
├── test_claude.py           # Direct Claude smoke test (Week 5)
├── prompt_experiments.py    # System-prompt comparison (Week 5)
├── reflection.md            # Week 6 reflection
├── docker-compose.yml       # Postgres service
└── requirements.txt
```

## What changed from Week 5

- Added `requests` to `requirements.txt` (in case future tools need HTTP)
- New file: `agent.py` with the tool schemas, tool functions, and the agent loop
- `schemas.py` gained `AgentRequest`, `AgentStep`, `AgentResponse`
- `main.py` imports `run_agent` and exposes `/ai/agent`
- Bumped FastAPI app version to "4.0.0 (Agent)"
