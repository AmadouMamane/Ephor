"""Couche 1 — configuration. Lit l'environnement, valide, expose des valeurs typées."""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    env: str
    log_level: str

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            env=os.getenv("APP_ENV", "local"),
            log_level=os.getenv("APP_LOG_LEVEL", "INFO"),
        )
