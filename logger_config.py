from __future__ import annotations

import logging
from typing import Optional


def configure_logging(level: Optional[int] = None) -> None:
    """Configure shared logging format for the bot."""
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(
        level=level or logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
