from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+asyncpg://guardian:guardian_pass@localhost:5432/guardian_monitor"
    )
    redis_url: str = "redis://localhost:6379/0"
    backend_url: str = "http://localhost:8000"

    model_config = {"env_file": ".env"}


settings = Settings()
