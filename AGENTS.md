# Repository Guidelines

## Project Shape

- `backend/`: FastAPI API, SQLAlchemy models, Alembic migrations, judge services, pytest tests.
- `frontend/`: React + TypeScript + Vite UI.
- `examples/`: importable sample problem directories and zip packages.
- `docs/`: local development, package format, and judge notes.

## Local Commands

Backend:

```bash
cd /Users/lmhhh/Desktop/sql/backend
source .venv/bin/activate
python -m app.scripts.setup_db
alembic upgrade head
pytest
uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd /Users/lmhhh/Desktop/sql/frontend
npm install
npm run build
npm run dev -- --host 127.0.0.1 --port 5173
```

## Implementation Notes

- Keep the V1 local-first architecture: FastAPI process starts in-process background judging; do not introduce Redis, Celery, Docker Compose, or remote services unless requested.
- User SQL is intentionally allowed to contain DML and DDL. Preserve the temporary PostgreSQL database isolation and high-risk SQL guard when changing judge code.
- `run` uses public test cases only; `submit` uses all test cases and must not leak hidden input, expected output, or actual output to non-admin users.
- `expected.csv` uses `\N` for SQL `NULL`. The literal text `NULL` is a string.
- `ordered` controls row ordering only. Column names and column order are part of the expected output contract.

## Editing Rules

- Do not commit `.env`, `.venv`, `node_modules`, `dist`, pytest caches, or local assistant/editor state.
- Prefer focused changes that match the existing code style.
- Run backend tests and frontend build before finalizing changes that touch both sides.
