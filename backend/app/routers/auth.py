from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import (
    LoginRequest,
    TokenResponse,
    UserCreate,
    UserRead,
    canonical_username,
)
from app.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
_REGISTRATION_LOCK_ID = 0x53514C4F4A


def _prevent_registration_races(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _REGISTRATION_LOCK_ID},
        )


def _set_token_response_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.post("/register", response_model=TokenResponse)
def register(
    payload: UserCreate, response: Response, db: Session = Depends(get_db)
) -> TokenResponse:
    password_hash = hash_password(payload.password)
    _prevent_registration_races(db)
    existing = db.scalar(select(User).where(User.username == payload.username))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username already exists"
        )

    user_count = db.scalar(select(func.count(User.id))) or 0
    user = User(
        username=payload.username,
        password_hash=password_hash,
        role="admin" if user_count == 0 else "user",
    )
    try:
        db.add(user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username already exists"
        ) from exc
    db.refresh(user)
    _set_token_response_headers(response)
    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest, response: Response, db: Session = Depends(get_db)
) -> TokenResponse:
    # Exact lookup preserves legacy V1 usernames. Only when it misses may a
    # valid modern canonical form be considered, preventing two colliding
    # accounts from being selected based on password rather than username.
    user = db.scalar(select(User).where(User.username == payload.username))
    normalized_username = canonical_username(payload.username)
    if (
        user is None
        and normalized_username is not None
        and normalized_username != payload.username
    ):
        user = db.scalar(select(User).where(User.username == normalized_username))
    password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_is_valid = verify_password(payload.password, password_hash)
    if user is None or not password_is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        db.commit()
    _set_token_response_headers(response)
    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.get("/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
