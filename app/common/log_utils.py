import json
import logging
import logging.config
import os
import sys
import threading
import types

from app.common import tracing


# Adds additional ECS fields to the logger.
class ExtraFieldsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = tracing.ctx_trace_id.get("")
        req = tracing.ctx_request.get(None)
        resp = tracing.ctx_response.get(None)

        if trace_id:
            record.trace = {"id": trace_id}  # type: ignore[attr-defined]

        http: dict[str, object] = {}
        if req:
            record.url = {"path": req.get("path", None)}  # type: ignore[attr-defined]
            http["request"] = {"method": req.get("method", None)}
        if resp:
            http["response"] = resp
        if http:
            record.http = http  # type: ignore[attr-defined]
        return True


class EndpointFilter(logging.Filter):
    def __init__(self, path: str, name: str = "") -> None:
        super().__init__(name)
        self._path = path

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find(self._path) == -1


_logger = logging.getLogger(__name__)

# Only `event.*` and `error.*` survive the platform's ingestion; `error.*` is
# filled in from `exc_info` by the ECS formatter.
_UNCAUGHT_EVENT = {
    "type": "uncaught_exception",
    "category": "process",
    "outcome": "failure",
}


def _ensure_logging_configured() -> None:
    """Apply `LOG_CONFIG` if nothing has configured logging yet.

    uvicorn applies the config only once it starts, so a failure before that (such
    as invalid settings) would otherwise be logged with no handler at all.
    """
    if logging.getLogger().handlers:
        return

    path = os.environ.get("LOG_CONFIG")
    if path:
        with open(path, encoding="utf-8") as config_file:
            logging.config.dictConfig(json.load(config_file))
    else:
        logging.basicConfig(level=logging.INFO)


def _log_uncaught(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: types.TracebackType | None,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    _ensure_logging_configured()

    _logger.critical(
        "Uncaught exception: %s",
        exc_value,
        exc_info=(exc_type, exc_value, exc_traceback),
        extra={"event": _UNCAUGHT_EVENT},
    )


def _log_uncaught_in_thread(args: threading.ExceptHookArgs) -> None:
    if args.exc_value is None or issubclass(args.exc_type, SystemExit):
        return

    _ensure_logging_configured()

    _logger.critical(
        "Uncaught exception in thread %s: %s",
        args.thread.name if args.thread else "unknown",
        args.exc_value,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        extra={"event": _UNCAUGHT_EVENT},
    )


def install_excepthooks() -> None:
    """Send uncaught exceptions to the logger instead of a bare traceback.

    Without this, Python prints them to stderr as plain text, which the ECS
    pipeline cannot parse into `error.*` fields.
    """
    sys.excepthook = _log_uncaught
    threading.excepthook = _log_uncaught_in_thread
