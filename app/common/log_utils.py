import copy
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


class AccessLogQueryFilter(logging.Filter):
    """Drop the query string from uvicorn's access log lines.

    uvicorn logs the path with its query string as the third argument of each
    `uvicorn.access` record; query values must not reach the logs. Returns a
    stripped copy for the handler to emit, leaving the original record untouched
    for any other handler.
    """

    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        if record.name != "uvicorn.access" or not isinstance(record.args, tuple):
            return record

        args = list(record.args)
        if len(args) <= 2 or not isinstance(args[2], str) or "?" not in args[2]:  # noqa: PLR2004
            return record

        args[2] = args[2].split("?", 1)[0]
        stripped = copy.copy(record)
        stripped.args = tuple(args)
        return stripped


_logger = logging.getLogger(__name__)

# ECS categorisation: `kind`, `category`, `type` and `outcome` take only ECS's
# allowed values, so our own name for the event goes in `action`. `error.*` is
# filled in from `exc_info` by the ECS formatter.
#
# The hooks log at ERROR, not CRITICAL: CDP normalises only .NET's "Critical" to
# "error", so a `critical` line would slip past anything filtering on
# `log.level: error`. `event.action` is what marks these as uncaught.
_UNCAUGHT_EVENT = {
    "kind": "event",
    "category": "process",
    "type": "error",
    "action": "uncaught_exception",
    "outcome": "failure",
}


def configure_logging() -> None:
    """Apply `LOG_CONFIG` if nothing has configured logging yet.

    uvicorn applies the config only once it starts, so a failure before that (such
    as invalid settings) would otherwise be logged with no handler at all. If
    `LOG_CONFIG` cannot be applied, falls back to basic logging rather than raising,
    because this also runs inside the exception hooks.
    """
    if logging.getLogger().handlers:
        return

    path = os.environ.get("LOG_CONFIG")
    if not path:
        logging.basicConfig(level=logging.INFO)
        return

    try:
        with open(path, encoding="utf-8") as config_file:
            logging.config.dictConfig(json.load(config_file))
    except OSError, ValueError:
        logging.basicConfig(level=logging.INFO)
        _logger.exception("Could not apply LOG_CONFIG %s, using basic logging", path)


def _log_uncaught(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: types.TracebackType | None,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    configure_logging()

    _logger.error(
        "Uncaught exception: %s",
        exc_value,
        exc_info=(exc_type, exc_value, exc_traceback),
        extra={"event": _UNCAUGHT_EVENT},
    )


def _log_uncaught_in_thread(args: threading.ExceptHookArgs) -> None:
    if args.exc_value is None or issubclass(args.exc_type, SystemExit):
        return

    configure_logging()

    _logger.error(
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
