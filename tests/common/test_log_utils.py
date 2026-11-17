import json
import logging
import logging.config
import pathlib
import sys
import threading

from app.common import log_utils


def test_extra_fields_filter_with_all_context(mocker):
    mock_trace_id = mocker.patch("app.common.tracing.ctx_trace_id")
    mock_request = mocker.patch("app.common.tracing.ctx_request")
    mock_response = mocker.patch("app.common.tracing.ctx_response")

    # Set context values
    mock_trace_id.get.return_value = "test-trace-id"
    mock_request.get.return_value = {"path": "/test", "method": "GET"}
    mock_response.get.return_value = {"status_code": 200}

    # Create a log record
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="test message",
        args=(),
        exc_info=None,
    )

    log_filter = log_utils.ExtraFieldsFilter()
    result = log_filter.filter(record)

    # Assertions
    assert result is True
    assert record.trace == {"id": "test-trace-id"}
    assert record.url == {"path": "/test"}
    assert record.http == {
        "request": {"method": "GET"},
        "response": {"status_code": 200},
    }


def test_extra_fields_filter_with_no_context(mocker):
    mock_trace_id = mocker.patch("app.common.tracing.ctx_trace_id")
    mock_request = mocker.patch("app.common.tracing.ctx_request")
    mock_response = mocker.patch("app.common.tracing.ctx_response")

    mock_trace_id.get.return_value = None
    mock_request.get.return_value = None
    mock_response.get.return_value = None

    # Create a log record
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="test message",
        args=(),
        exc_info=None,
    )

    log_filter = log_utils.ExtraFieldsFilter()
    result = log_filter.filter(record)

    assert result is True
    assert not hasattr(record, "trace")
    assert not hasattr(record, "url")
    assert not hasattr(record, "http")


def test_endpoint_filter_blocks_matching_path():
    filter_path = "/health"
    log_filter = log_utils.EndpointFilter(path=filter_path)

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg=f"GET {filter_path} HTTP/1.1",
        args=(),
        exc_info=None,
    )

    assert log_filter.filter(record) is False


def test_endpoint_filter_allows_non_matching_path():
    filter_path = "/health"
    log_filter = log_utils.EndpointFilter(path=filter_path)

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="GET /api/users HTTP/1.1",
        args=(),
        exc_info=None,
    )

    assert log_filter.filter(record) is True


def _raised() -> tuple[type[BaseException], BaseException, object]:
    try:
        msg = "boom"
        raise ValueError(msg)
    except ValueError as error:
        return type(error), error, error.__traceback__


def test_uncaught_exception_is_logged_with_traceback(caplog):
    exc_type, exc_value, tb = _raised()

    with caplog.at_level(logging.ERROR):
        log_utils._log_uncaught(exc_type, exc_value, tb)  # type: ignore[arg-type]

    record = caplog.records[-1]

    assert record.levelno == logging.ERROR
    assert record.exc_info == (exc_type, exc_value, tb)
    assert record.event == {  # type: ignore[attr-defined]
        "kind": "event",
        "category": "process",
        "type": "error",
        "action": "uncaught_exception",
        "outcome": "failure",
    }


def test_uncaught_thread_exception_is_logged_with_traceback(caplog):
    exc_type, exc_value, tb = _raised()

    args = threading.ExceptHookArgs(
        (exc_type, exc_value, tb, threading.current_thread())
    )

    with caplog.at_level(logging.ERROR):
        log_utils._log_uncaught_in_thread(args)

    record = caplog.records[-1]

    assert record.levelno == logging.ERROR
    assert record.exc_info == (exc_type, exc_value, tb)


def test_install_excepthooks_replaces_the_defaults(mocker):
    import sys

    mocker.patch.object(sys, "excepthook", sys.excepthook)
    mocker.patch.object(threading, "excepthook", threading.excepthook)

    log_utils.install_excepthooks()

    assert sys.excepthook is log_utils._log_uncaught
    assert threading.excepthook is log_utils._log_uncaught_in_thread


def test_keyboard_interrupt_goes_to_the_default_hook(mocker, caplog):
    default_hook = mocker.patch.object(sys, "__excepthook__")
    error = KeyboardInterrupt()

    with caplog.at_level(logging.ERROR):
        log_utils._log_uncaught(KeyboardInterrupt, error, None)

    default_hook.assert_called_once_with(KeyboardInterrupt, error, None)
    assert caplog.records == []


def test_thread_ending_with_system_exit_is_not_logged(caplog):
    args = threading.ExceptHookArgs(
        (SystemExit, SystemExit(), None, threading.current_thread())
    )

    with caplog.at_level(logging.ERROR):
        log_utils._log_uncaught_in_thread(args)

    assert caplog.records == []


def test_thread_hook_without_an_exception_is_not_logged(caplog):
    args = threading.ExceptHookArgs((SystemExit, None, None, None))

    with caplog.at_level(logging.ERROR):
        log_utils._log_uncaught_in_thread(args)

    assert caplog.records == []


def _unconfigured(mocker):
    """Hide the root handlers and stub out both ways of configuring logging."""
    mocker.patch.object(logging.getLogger(), "handlers", [])
    dict_config = mocker.patch.object(logging.config, "dictConfig")
    basic_config = mocker.patch.object(logging, "basicConfig")
    return dict_config, basic_config


def test_configure_logging_leaves_existing_config_alone(mocker):
    mocker.patch.object(logging.getLogger(), "handlers", [logging.NullHandler()])
    dict_config = mocker.patch.object(logging.config, "dictConfig")
    basic_config = mocker.patch.object(logging, "basicConfig")

    log_utils.configure_logging()

    dict_config.assert_not_called()
    basic_config.assert_not_called()


def test_configure_logging_applies_log_config(mocker, monkeypatch, tmp_path):
    dict_config, basic_config = _unconfigured(mocker)
    config = {"version": 1}
    path = tmp_path / "logging.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("LOG_CONFIG", str(path))

    log_utils.configure_logging()

    dict_config.assert_called_once_with(config)
    basic_config.assert_not_called()


def test_configure_logging_without_log_config_uses_basic_config(mocker, monkeypatch):
    dict_config, basic_config = _unconfigured(mocker)
    monkeypatch.delenv("LOG_CONFIG", raising=False)

    log_utils.configure_logging()

    dict_config.assert_not_called()
    basic_config.assert_called_once_with(level=logging.INFO)


def test_configure_logging_falls_back_when_log_config_is_missing(
    mocker, monkeypatch, tmp_path
):
    _, basic_config = _unconfigured(mocker)
    logger = mocker.patch.object(log_utils, "_logger")
    monkeypatch.setenv("LOG_CONFIG", str(tmp_path / "missing.json"))

    log_utils.configure_logging()

    basic_config.assert_called_once_with(level=logging.INFO)
    logger.exception.assert_called_once()


def test_configure_logging_falls_back_when_log_config_is_invalid(
    mocker, monkeypatch, tmp_path
):
    _, basic_config = _unconfigured(mocker)
    mocker.patch.object(log_utils, "_logger")
    path = tmp_path / "logging.json"
    path.write_text("not json")
    monkeypatch.setenv("LOG_CONFIG", str(path))

    log_utils.configure_logging()

    basic_config.assert_called_once_with(level=logging.INFO)


def _access_record(path: str, name: str = "uvicorn.access") -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:5000", "GET", path, "1.1", 200),
        exc_info=None,
    )


def test_access_log_query_filter_drops_the_query_string():
    record = _access_record("/guidance?nextCursor=abc&scheme=sfi")

    result = log_utils.AccessLogQueryFilter().filter(record)

    assert result.getMessage() == '127.0.0.1:5000 - "GET /guidance HTTP/1.1" 200'


def test_access_log_query_filter_leaves_the_original_record_untouched():
    record = _access_record("/guidance?scheme=sfi")

    log_utils.AccessLogQueryFilter().filter(record)

    assert record.args is not None
    assert record.args[2] == "/guidance?scheme=sfi"


def test_access_log_query_filter_keeps_a_path_without_a_query():
    record = _access_record("/guidance")

    result = log_utils.AccessLogQueryFilter().filter(record)

    assert result is record


def test_access_log_query_filter_ignores_other_loggers():
    record = _access_record("/guidance?scheme=sfi", name="app")

    result = log_utils.AccessLogQueryFilter().filter(record)

    assert result is record
    assert record.args is not None
    assert record.args[2] == "/guidance?scheme=sfi"


def test_access_log_query_filter_strips_the_emitted_line(capsys):
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(log_utils.AccessLogQueryFilter())

    handler.handle(_access_record("/guidance?scheme=sfi"))

    assert capsys.readouterr().out == (
        '127.0.0.1:5000 - "GET /guidance HTTP/1.1" 200\n'
    )


def test_production_logging_config_filters_access_log_queries():
    config = json.loads(pathlib.Path("logging.json").read_text(encoding="utf-8"))
    handler_filters = config["handlers"]["console"]["filters"]
    filters = config["filters"]

    assert any(
        filters[name]["()"] == "app.common.log_utils.AccessLogQueryFilter"
        for name in handler_filters
    )
