from __future__ import annotations

import json
import logging

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.config import Settings, get_settings
from src.logging_utils import configure_logging
from src.pipeline.embedder import ReportEmbedder
from src.rag.chain import ReportQAChain
from src.rag.retriever import ReportRetriever
from src.slack.handlers import register_handlers

logger = logging.getLogger(__name__)


def build_qa_chain(settings: Settings) -> ReportQAChain:
    vectorstore = ReportEmbedder(
        api_key=settings.upstage_api_key or "",
        persist_directory=settings.chroma_persist_dir,
        collection_name=settings.chroma_collection_name,
        embedding_model=settings.embedding_model,
    ).get_vectorstore()

    retriever = ReportRetriever(
        vectorstore=vectorstore,
        openai_api_key=settings.openai_api_key or "",
        llm_model=settings.llm_model,
    )
    return ReportQAChain(
        retriever=retriever,
        openai_api_key=settings.openai_api_key or "",
        llm_model=settings.llm_model,
    )


def create_app(settings: Settings | None = None, qa_chain: ReportQAChain | None = None) -> App:
    app_settings = settings or get_settings()
    app_settings.validate_slack_settings()

    chain = qa_chain or build_qa_chain(app_settings)
    app = App(
        token=app_settings.slack_bot_token or "",
        signing_secret=app_settings.slack_signing_secret or "",
    )
    register_handlers(
        app,
        qa_chain=chain,
        allowed_channel_ids=app_settings.allowed_channel_ids,
        allowed_user_ids=app_settings.allowed_user_ids,
    )
    logger.info(
        "Slack app initialized allowed_channels=%d allowed_users=%d",
        len(app_settings.allowed_channel_ids),
        len(app_settings.allowed_user_ids),
    )
    return app


def start_socket_mode(app: App, *, app_token: str) -> None:
    handler = SocketModeHandler(app, app_token)

    def _log_socket_message(message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return

        envelope_type = payload.get("type")
        if envelope_type not in {"events_api", "interactive", "slash_commands", "disconnect", "hello"}:
            return

        event_type = None
        body = payload.get("payload")
        if isinstance(body, dict):
            event = body.get("event")
            if isinstance(event, dict):
                event_type = event.get("type")

        logger.info(
            "Socket envelope received type=%s event_type=%s envelope_id=%s",
            envelope_type,
            event_type,
            payload.get("envelope_id"),
        )

    def _log_socket_error(error: Exception) -> None:
        logger.exception("Socket client error: %s", error)

    def _log_socket_close(code: int, reason: str | None) -> None:
        logger.warning("Socket connection closed code=%s reason=%s", code, reason)

    handler.client.on_message_listeners.append(_log_socket_message)
    handler.client.on_error_listeners.append(_log_socket_error)
    handler.client.on_close_listeners.append(_log_socket_close)
    handler.start()


def main() -> None:
    settings = get_settings()
    settings.validate_slack_settings()
    configure_logging(level=settings.log_level)

    app = create_app(settings=settings)
    start_socket_mode(app, app_token=settings.slack_app_token or "")


if __name__ == "__main__":
    main()
