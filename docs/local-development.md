# Local Development

## Backend

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

The setup script creates:

- application database: `sql_oj`
- restricted judge role: `oj_runner`

## Frontend

```bash
cd /Users/lmhhh/Desktop/sql/frontend
npm install
npm run dev
```

Open http://localhost:5173.

The Vite dev server proxies `/api` to `http://127.0.0.1:8000`.

