from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://forge:forge@postgres:5432/forge"
    redis_url: str = "redis://redis:6379/0"
    rabbitmq_url: str = "amqp://forge:forge@rabbitmq:5672/"
    rabbitmq_mgmt_url: str = "http://forge:forge@rabbitmq:15672"
    checkout_api_url: str = "http://checkout-api:8000"
    inventory_api_url: str = "http://inventory-api:8000"
    worker_api_url: str = "http://fulfillment-worker:8000"
    chaos_token: str = "dev-token"
    require_chaos_token: bool = False
    injection_ttl_default_s: int = 300
    injection_ttl_max_s: int = 3600


settings = Settings()
