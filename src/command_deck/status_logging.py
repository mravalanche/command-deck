"""Small, bounded connection-status messages for container stdout."""

import logging
import sys

from command_deck.models import Telemetry

logger = logging.getLogger(__name__)


class _CurrentStdoutHandler(logging.StreamHandler):
    """Use the active stdout stream, including during captured test runs."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def configure_stdout_logging() -> None:
    package_logger = logging.getLogger("command_deck")
    if not any(isinstance(handler, _CurrentStdoutHandler) for handler in package_logger.handlers):
        handler = _CurrentStdoutHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s command-deck: %(message)s"))
        package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    package_logger.propagate = False


class StatusLog:
    """Log the first result and later state changes, without repeating steady failures."""

    def __init__(self, source: str):
        self.source = source
        self.previous: Telemetry | None = None

    def update(self, state: Telemetry) -> None:
        if state == self.previous:
            return
        self.previous = state
        level = (
            logging.INFO
            if state in {Telemetry.CONNECTED, Telemetry.NOT_CONFIGURED}
            else logging.WARNING
        )
        logger.log(level, "%s: %s", self.source, state.value.lower().replace("_", " "))
