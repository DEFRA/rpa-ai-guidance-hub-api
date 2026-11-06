"""What of the API's queue in SQS needs no queue: how it is reached."""

from __future__ import annotations

from unittest import mock

import boto3

from app import config as app_config
from app.background import sqs


def test_the_client_waits_longer_for_an_answer_than_a_long_poll_lasts():
    config = mock.Mock(floci_endpoint_url=None, aws_region="eu-west-2")

    with (
        mock.patch.object(app_config, "get_config", return_value=config),
        mock.patch.object(boto3, "client") as making_a_client,
    ):
        sqs.client()

    settings = making_a_client.call_args.kwargs["config"]
    assert settings.read_timeout > 20  # noqa: PLR2004 - SQS's longest poll
