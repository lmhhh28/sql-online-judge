from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_database_url: str = "postgresql+psycopg://lmhhh@/sql_oj?host=/tmp"
    admin_database_url: str = "postgresql+psycopg://lmhhh@/postgres?host=/tmp"
    judge_runner_role: str = "oj_runner"
    jwt_secret_key: str = "change-this-dev-secret"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 10080
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

