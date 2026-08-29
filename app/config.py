import os
from typing import Dict, Any
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Provider API Keys
    GEMINI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None

    # Ollama endpoint
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # Database & Redis configurations
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/gateway"
    REDIS_URL: str = "redis://localhost:6379/0"

    # Admin Auth Configuration
    ADMIN_API_KEY: str = "admin_secret_key_here"

    # Testing & Performance Simulation
    MOCK_PROVIDERS: bool = False

settings = Settings()
