from __future__ import annotations

import logging

from src.security import SecretFilter


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if not any(isinstance(existing, SecretFilter) for existing in handler.filters):
            handler.addFilter(SecretFilter())

