from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002
    ibkr_client_id: int = 1

    log_level: str = "INFO"
    log_dir: Path = Path("./logs")
    data_dir: Path = Path("./data")

    dry_run: bool = True

    strategy_symbol: str = "SPY"
    strategy_qty: int = 1

    def ensure_dirs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.db"


def load_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
