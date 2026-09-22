"""Конфигурация приложения (Stage 9).

Загружает переменные окружения для безопасного управления секретами.
Никаких хардкодов секретов в коде.
"""
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost/connector"
    
    # Blockchain Providers (Read-Only URLs)
    ETHEREUM_RPC_URL: str = "https://eth-mainnet.example.com"
    TRON_RPC_URL: str = "https://tron-mainnet.example.com"
    
    # AML Provider
    AML_API_KEY: Optional[str] = None
    AML_API_URL: str = "https://aml-provider.example.com/api"
    
    # Monitoring
    ALERT_WEBHOOK_URL: Optional[str] = None
    INDEXER_LAG_THRESHOLD: int = 10
    SENT_TIMEOUT_MINUTES: int = 30
    
    # App
    ENVIRONMENT: str = "development"  # development | staging | production
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"

settings = Settings()
