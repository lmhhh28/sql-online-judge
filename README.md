# SQL Online Judge

Local-first SQL practice online judge built with FastAPI, PostgreSQL, and React.

## Quick Start

### 1. Backend

```bash
cd /Users/lmhhh/Desktop/sql/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
python -m app.scripts.setup_db
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend

```bash
cd /Users/lmhhh/Desktop/sql/frontend
npm install
npm run dev
```

Open http://localhost:5173.

The first registered user becomes an administrator.

## Sample Problems

Import any zip package under `examples/` from the admin page after registering the first account.

