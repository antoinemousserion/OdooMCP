"""Logging des durées d'exécution (tools MCP + ripgrep)."""

from __future__ import annotations

import functools
import logging
import os
import sys
import time
from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

LOGGER = logging.getLogger("odoo_mcp")


def setup_logging(level: int | None = None) -> None:
    if LOGGER.handlers:
        return

    log_level = level if level is not None else logging.INFO
    env_level = os.environ.get("MCP_LOG_LEVEL", "").upper()
    if env_level and hasattr(logging, env_level):
        log_level = getattr(logging, env_level)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(log_level)
    LOGGER.propagate = False


def log_tool_duration(func: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = time.perf_counter()
        LOGGER.info("tool %s start", func.__name__)
        try:
            result = func(*args, **kwargs)
        except Exception:
            LOGGER.exception("tool %s failed after %.1f ms", func.__name__, (time.perf_counter() - start) * 1000)
            raise
        else:
            LOGGER.info("tool %s ok in %.1f ms", func.__name__, (time.perf_counter() - start) * 1000)
            return result

    return wrapper
