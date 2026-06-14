"""Point the app at a throwaway SQLite DB before `database`/`agent`/`main` import."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="agent-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
