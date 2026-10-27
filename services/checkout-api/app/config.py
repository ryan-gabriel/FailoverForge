from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://forge:forge@postgres:5432/forge"
    redis_url: str = "redis://redis:6379/0"
    rabbitmq_url: str = "amqp://forge:forge@rabbitmq:5672/"
    inventory_api_url: str = "http://inventory-api:8000"


settings = Settings()
