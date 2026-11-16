from app.common import metrics


def test_counter_success(mocker):
    mock_put_metric = mocker.patch("app.common.metrics.__put_metric")

    metrics.counter("test_metric", 123)

    mock_put_metric.assert_called_once_with("test_metric", 123, "Count")


def test_counter_handles_exception(mocker):
    mocker.patch("app.common.metrics.__put_metric", side_effect=Exception("Test Error"))
    mock_logger = mocker.patch("app.common.metrics.logger")

    metrics.counter("test_metric", 123)

    mock_logger.exception.assert_called_once_with("Error calling put_metric")
