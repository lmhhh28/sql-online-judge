import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.deps import get_current_user
from app.models import User
from app.routers import admin, auth, problems
from app.schemas import UserRead
from app.services.judge import recover_interrupted_judges

app = FastAPI(title="SQL Online Judge", version="0.1.0")
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(problems.router)
app.include_router(admin.router)


@app.on_event("startup")
def recover_judge_state() -> None:
    try:
        submissions, databases = recover_interrupted_judges()
    except Exception:
        logger.warning("Judge recovery failed during startup", exc_info=True)
        return
    if submissions or databases:
        logger.info("Recovered %s interrupted submissions and dropped %s stale judge databases", submissions, databases)


@app.get("/api/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
