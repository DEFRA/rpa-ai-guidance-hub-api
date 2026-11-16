import logging

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

    with caplog.at_level(logging.CRITICAL):
        log_utils._log_uncaught(exc_type, exc_value, tb)  # type: ignore[arg-type]

    record = caplog.records[-1]

    assert record.levelno == logging.CRITICAL
    assert record.exc_info == (exc_type, exc_value, tb)
    assert record.event["outcome"] == "failure"  # type: ignore[attr-defined]


def test_uncaught_thread_exception_is_logged_with_traceback(caplog):
    import threading

    exc_type, exc_value, tb = _raised()

    args = threading.ExceptHookArgs(
        (exc_type, exc_value, tb, threading.current_thread())
    )

    with caplog.at_level(logging.CRITICAL):
        log_utils._log_uncaught_in_thread(args)

    assert caplog.records[-1].exc_info == (exc_type, exc_value, tb)


def test_install_excepthooks_replaces_the_defaults(mocker):
    import sys
    import threading

    mocker.patch.object(sys, "excepthook", sys.excepthook)
    mocker.patch.object(threading, "excepthook", threading.excepthook)

    log_utils.install_excepthooks()

    assert sys.excepthook is log_utils._log_uncaught
    assert threading.excepthook is log_utils._log_uncaught_in_thread
