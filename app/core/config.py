from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "EGX Trading Platform"
    environment: str = "development"
    version: str = "0.1.0"
    timezone: str = "Africa/Cairo"


settings = Settings()
