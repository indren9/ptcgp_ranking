import io
import logging

import pytest

from reporting.logs import MANAGED_HANDLER_ATTR, configure_logging


@pytest.fixture
def restore_ptcgp_logger():
    logger = logging.getLogger("ptcgp")
    net_logger = logging.getLogger("ptcgp.net")
    old_handlers = list(logger.handlers)
    old_level = logger.level
    old_propagate = logger.propagate
    old_net_level = net_logger.level

    for handler in old_handlers:
        logger.removeHandler(handler)

    yield

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    for handler in old_handlers:
        logger.addHandler(handler)
    logger.setLevel(old_level)
    logger.propagate = old_propagate
    net_logger.setLevel(old_net_level)


def _managed_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [handler for handler in logger.handlers if getattr(handler, MANAGED_HANDLER_ATTR, False)]


def test_configure_logging_is_idempotent(restore_ptcgp_logger):
    stream = io.StringIO()

    logger = configure_logging("DEBUG", stream=stream)
    configure_logging("INFO", stream=io.StringIO())

    managed = _managed_handlers(logger)
    assert len(managed) == 1
    assert logger.level == logging.INFO
    assert logger.propagate is False
    assert managed[0].level == logging.INFO

    logger.info("ciao log")
    assert "ciao log" in stream.getvalue()


def test_configure_logging_quiets_network_logger(restore_ptcgp_logger):
    configure_logging("INFO", quiet_http=True)

    assert logging.getLogger("ptcgp.net").level == logging.WARNING
    assert logging.getLogger("WDM").level == logging.WARNING


def test_configure_logging_force_replaces_managed_handler(restore_ptcgp_logger):
    first_stream = io.StringIO()
    second_stream = io.StringIO()

    logger = configure_logging("INFO", stream=first_stream)
    configure_logging("INFO", stream=second_stream, force=True)

    assert len(_managed_handlers(logger)) == 1
    logger.info("nuovo handler")
    assert "nuovo handler" not in first_stream.getvalue()
    assert "nuovo handler" in second_stream.getvalue()
