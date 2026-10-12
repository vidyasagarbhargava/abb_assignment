"""Runtime configuration. Every knob is overridable via MLOPS_* environment variables."""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MLOPS_", env_file=".env", extra="ignore")

    database_url: str = Field(
        default="sqlite:///./mlops.db",
        validation_alias=AliasChoices("DATABASE_URL", "MLOPS_DATABASE_URL"),
    )
    deployment_env: str = "local"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:4200"]

    # Worker / queue
    embedded_worker: bool = True
    worker_poll_interval_s: float = 0.5
    worker_lease_s: int = 60
    job_max_attempts: int = 3
    retry_backoff_base_s: float = 2.0
    runtime_timeout_s: float = 30.0

    # Governance
    require_staging_before_production: bool = True
    allow_failure_simulation: bool = True
    allowed_artifact_schemes: list[str] = ["s3", "gs", "abfss", "file"]
    default_runtime: str = "simulated"

    # Monitoring thresholds
    max_metric_rows_per_query: int = 50_000
    max_metric_batch: int = 1_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
