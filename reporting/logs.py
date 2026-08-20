from __future__ import annotations

import logging
import sys
from typing import TextIO


MANAGED_HANDLER_ATTR = "_ptcgp_managed_handler"
DEFAULT_FORMAT = "%(levelname).1s | %(name)s | %(message)s"


class TqdmLoggingHandler(logging.StreamHandler):
    """Logging handler that plays nicely with an active tqdm progress bar."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            from tqdm import tqdm

            tqdm.write(msg, file=self.stream)
            self.flush()
        except Exception:
            self.handleError(record)


def _coerce_level(level: int | str | None) -> int:
    if isinstance(level, int):
        return level
    if level is None:
        return logging.INFO
    value = str(level).strip().upper()
    return getattr(logging, value, logging.INFO)


def configure_logging(
    level: int | str | None = "INFO",
    *,
    logger_name: str = "ptcgp",
    quiet_http: bool = True,
    force: bool = False,
    stream: TextIO | None = None,
    tqdm_compatible: bool = False,
) -> logging.Logger:
    """
    Configure the project logger once, safely across repeated notebook runs.

    The project logger does not propagate to root, which avoids duplicated
    Jupyter/basicConfig output. Repeated calls update the existing managed
    handler instead of stacking new handlers.
    """
    numeric_level = _coerce_level(level)
    logger = logging.getLogger(logger_name)
    logger.setLevel(numeric_level)
    logger.propagate = False

    managed_handlers = [h for h in logger.handlers if getattr(h, MANAGED_HANDLER_ATTR, False)]
    if force:
        for handler in managed_handlers:
            logger.removeHandler(handler)
            handler.close()
        managed_handlers = []

    if managed_handlers:
        handler = managed_handlers[0]
        for extra_handler in managed_handlers[1:]:
            logger.removeHandler(extra_handler)
            extra_handler.close()
    else:
        handler_cls = TqdmLoggingHandler if tqdm_compatible else logging.StreamHandler
        handler = handler_cls(stream or sys.stderr)
        setattr(handler, MANAGED_HANDLER_ATTR, True)
        logger.addHandler(handler)

    if tqdm_compatible and not isinstance(handler, TqdmLoggingHandler):
        logger.removeHandler(handler)
        handler.close()
        handler = TqdmLoggingHandler(stream or sys.stderr)
        setattr(handler, MANAGED_HANDLER_ATTR, True)
        logger.addHandler(handler)
    elif not tqdm_compatible and isinstance(handler, TqdmLoggingHandler):
        logger.removeHandler(handler)
        handler.close()
        handler = logging.StreamHandler(stream or sys.stderr)
        setattr(handler, MANAGED_HANDLER_ATTR, True)
        logger.addHandler(handler)

    handler.setLevel(numeric_level)
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))

    if quiet_http:
        logging.getLogger(f"{logger_name}.net").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("selenium").setLevel(logging.WARNING)
        logging.getLogger("WDM").setLevel(logging.WARNING)

    return logger


def get_logger(name: str = "ptcgp") -> logging.Logger:
    return logging.getLogger(name)


__all__ = ["configure_logging", "get_logger"]
