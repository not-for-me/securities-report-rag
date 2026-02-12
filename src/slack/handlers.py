from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from typing import Any

from slack_bolt import App

from src.models import QAResult
from src.rag.chain import ReportQAChain
from src.security import MAX_QUERY_LENGTH, RateLimiter, normalize_slack_text, validate_query

logger = logging.getLogger(__name__)


def extract_question(text: str) -> str:
    cleaned = re.sub(r"<@[A-Z0-9]+>", "", text)
    cleaned = normalize_slack_text(cleaned)
    return cleaned.strip()


def format_response(result: QAResult) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": result.answer},
        }
    ]

    if result.sources:
        source_lines = [
            f"- {source.get('broker', '알 수 없음')} | {source.get('analyst', '알 수 없음')} | "
            f"{source.get('date', '날짜 미상')} | {source.get('file', '파일 미상')}"
            for source in result.sources
        ]
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": ":page_facing_up: 출처\n" + "\n".join(source_lines),
                        }
                    ],
                },
            ]
        )
    return blocks


def register_handlers(
    app: App,
    *,
    qa_chain: ReportQAChain,
    allowed_channel_ids: Iterable[str] | None = None,
    allowed_user_ids: Iterable[str] | None = None,
    limiter: RateLimiter | None = None,
) -> None:
    allow_channels = set(allowed_channel_ids or [])
    allow_users = set(allowed_user_ids or [])
    rate_limiter = limiter or RateLimiter(max_requests=10, window_seconds=60)

    @app.middleware
    def log_incoming_event(
        logger: logging.Logger,
        body: dict[str, Any],
        next: Callable[[], Any],  # noqa: A002
    ) -> Any:
        event = body.get("event")
        if isinstance(event, dict):
            logger.info(
                "Slack event received type=%s channel=%s user=%s",
                event.get("type"),
                event.get("channel"),
                event.get("user"),
            )
        return next()

    def _is_authorized(event: dict[str, Any], say: Callable[..., Any]) -> bool:
        channel_id = event.get("channel")
        user_id = event.get("user", "")
        if allow_channels and channel_id not in allow_channels:
            logger.info("Ignored event from unauthorized channel channel=%s user=%s", channel_id, user_id)
            return False
        if allow_users and user_id not in allow_users:
            logger.info("Blocked event from unauthorized user user=%s channel=%s", user_id, channel_id)
            say("이 Bot을 사용할 권한이 없습니다.")
            return False
        return True

    def _handle_question(event: dict[str, Any], say: Callable[..., Any]) -> None:
        if event.get("bot_id"):
            logger.debug("Skipped bot-originated event channel=%s", event.get("channel"))
            return
        logger.info(
            "Processing question event_type=%s channel=%s user=%s",
            event.get("type"),
            event.get("channel"),
            event.get("user"),
        )
        if not _is_authorized(event, say):
            return

        user_id = event.get("user", "")
        if user_id and not rate_limiter.is_allowed(user_id):
            logger.info("Rate limited user=%s", user_id)
            say("요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")
            return

        question = extract_question(event.get("text", ""))
        validation_error = validate_query(question, max_length=MAX_QUERY_LENGTH)
        if validation_error:
            logger.info("Validation error for user=%s: %s", user_id, validation_error)
            say(validation_error)
            return

        result = qa_chain.ask(question)
        logger.info("Answer generated for user=%s", user_id)
        say(text=result.answer, blocks=format_response(result))

    @app.event("app_mention")
    def handle_mention(event: dict[str, Any], say: Callable[..., Any]) -> None:
        try:
            _handle_question(event, say)
        except Exception as error:  # noqa: BLE001
            logger.exception("Failed to handle app_mention: %s", error)
            say("죄송합니다. 답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")

    @app.event("message")
    def handle_dm(event: dict[str, Any], say: Callable[..., Any]) -> None:
        if event.get("channel_type") != "im":
            return

        try:
            _handle_question(event, say)
        except Exception as error:  # noqa: BLE001
            logger.exception("Failed to handle DM message: %s", error)
            say("죄송합니다. 요청 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
