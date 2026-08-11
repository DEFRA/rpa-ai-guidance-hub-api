from logging import getLogger
from typing import Any

from aws_embedded_metrics import metric_scope
from aws_embedded_metrics.storage_resolution import StorageResolution

logger = getLogger(__name__)


# This is using the aws_embedded_metrics library, which doesn't seem to be playing nicely with fastapi
# metrics.put_metric always seems to thrown an exception, even though the metrics are being sent to cloudwatch
# This is a related issue: https://github.com/awslabs/aws-embedded-metrics-python/issues/52
# More time needs to be spent on this, but for now, the metrics are being sent to cloudwatch
@metric_scope
def __put_metric(
    metric_name: str, value: int, unit: str, metrics: Any
) -> None:  # pragma: no cover
    logger.debug("put metric: %s - %s - %s", metric_name, value, unit)
    metrics.put_metric(metric_name, value, unit, StorageResolution.STANDARD)


def counter(metric_name: str, value: int) -> None:
    try:
        __put_metric(metric_name, value, "Count")  # type: ignore[call-arg]
    except Exception as e:
        logger.error("Error calling put_metric: %s", e)
