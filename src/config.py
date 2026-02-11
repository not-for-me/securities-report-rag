from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True, slots=True)
class Settings:
    """프로젝트 전역 설정."""

    env: str
    upstage_api_key: str | None
    openai_api_key: str | None
    slack_bot_token: str | None
    slack_app_token: str | None
    slack_signing_secret: str | None

    llm_model: str
    embedding_model: str
    chroma_persist_dir: str
    chroma_collection_name: str
    log_level: str
    upstage_parse_mode: str
    upstage_parse_endpoint: str

    allowed_channel_ids: list[str]
    allowed_user_ids: list[str]

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    @property
    def chroma_path(self) -> Path:
        return Path(self.chroma_persist_dir)

    def validate_pipeline_settings(self) -> None:
        missing = [
            name
            for name, value in (
                ("UPSTAGE_API_KEY", self.upstage_api_key),
                ("OPENAI_API_KEY", self.openai_api_key),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing required pipeline env vars: {', '.join(missing)}")

    def validate_slack_settings(self) -> None:
        missing = [
            name
            for name, value in (
                ("SLACK_BOT_TOKEN", self.slack_bot_token),
                ("SLACK_APP_TOKEN", self.slack_app_token),
                ("SLACK_SIGNING_SECRET", self.slack_signing_secret),
                ("OPENAI_API_KEY", self.openai_api_key),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing required Slack env vars: {', '.join(missing)}")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.getenv("ENV", "development"),
            upstage_api_key=os.getenv("UPSTAGE_API_KEY"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            slack_bot_token=os.getenv("SLACK_BOT_TOKEN"),
            slack_app_token=os.getenv("SLACK_APP_TOKEN"),
            slack_signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
            llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
            chroma_persist_dir=os.getenv("CHROMA_PERSIST_DIR", "./data/chromadb"),
            chroma_collection_name=os.getenv("CHROMA_COLLECTION_NAME", "securities_reports"),
            log_level=os.getenv("LOG_LEVEL", "DEBUG"),
            upstage_parse_mode=os.getenv("UPSTAGE_PARSE_MODE", "auto"),
            upstage_parse_endpoint=os.getenv(
                "UPSTAGE_PARSE_ENDPOINT",
                "https://api.upstage.ai/v1/document-digitization",
            ),
            allowed_channel_ids=_split_csv(os.getenv("ALLOWED_CHANNEL_IDS")),
            allowed_user_ids=_split_csv(os.getenv("ALLOWED_USER_IDS")),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()

