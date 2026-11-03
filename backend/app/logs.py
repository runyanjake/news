"""One log format for the whole service, uvicorn's own loggers included.

    2026-11-03T08:00:00.123Z INFO app.main: Generation job started

The Node renderer (frontend/src/log.js) writes the same format.
"""

import logging
import time

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class UTCFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802 (logging API)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
        return f"{stamp}.{int(record.msecs):03d}Z"


class _Handler(logging.StreamHandler):
    """Marks the handler we install so reconfiguring replaces it instead of stacking."""


def configure_logging(level: int = logging.INFO) -> None:
    handler = _Handler()
    handler.setFormatter(UTCFormatter(LOG_FORMAT))
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, _Handler)] + [handler]
    root.setLevel(level)
    # uvicorn installs its own handlers and formats; route its records through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # httpx logs every request at INFO; fetch failures are already reported by the pipeline.
    logging.getLogger("httpx").setLevel(logging.WARNING)
