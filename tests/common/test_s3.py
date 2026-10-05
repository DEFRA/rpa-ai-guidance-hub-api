from unittest import mock
from unittest.mock import MagicMock

import boto3

from app.common import s3


class TestGetS3Client:
    def test_creates_working_client_against_configured_endpoint_and_region(
        self, monkeypatch
    ):
        monkeypatch.setattr(s3, "client", None)
        mock_config = MagicMock()
        mock_config.floci_endpoint_url = "http://floci:4566"
        mock_config.aws_region = "eu-west-2"
        monkeypatch.setattr("app.config.get_config", lambda: mock_config)

        mock_boto3_client = MagicMock()
        monkeypatch.setattr(boto3, "client", mock_boto3_client)

        client = s3.get_s3_client()

        assert client is mock_boto3_client.return_value
        mock_boto3_client.assert_called_once_with(
            "s3",
            endpoint_url="http://floci:4566",
            region_name="eu-west-2",
            config=mock.ANY,
        )

    def test_passes_none_endpoint_when_unconfigured(self, monkeypatch):
        """No floci_endpoint_url: falls through to default endpoint resolution."""
        monkeypatch.setattr(s3, "client", None)
        mock_config = MagicMock()
        mock_config.floci_endpoint_url = None
        mock_config.aws_region = "eu-west-2"
        monkeypatch.setattr("app.config.get_config", lambda: mock_config)

        mock_boto3_client = MagicMock()
        monkeypatch.setattr(boto3, "client", mock_boto3_client)

        client = s3.get_s3_client()

        assert client is mock_boto3_client.return_value
        mock_boto3_client.assert_called_once_with(
            "s3",
            endpoint_url=None,
            region_name="eu-west-2",
            config=mock.ANY,
        )

    def test_returns_existing_cached_singleton_instance(self, monkeypatch):
        sentinel_client = object()
        monkeypatch.setattr(s3, "client", sentinel_client)

        result = s3.get_s3_client()

        assert result is sentinel_client

    def test_bounds_each_call_by_half_a_web_request_with_one_retry(self, monkeypatch):
        """The UI waits 5 seconds for this service; one S3 call gets half of that
        to connect and half to answer, and one retry."""
        monkeypatch.setattr(s3, "client", None)
        mock_config = MagicMock()
        mock_config.floci_endpoint_url = None
        mock_config.aws_region = "eu-west-2"
        monkeypatch.setattr("app.config.get_config", lambda: mock_config)
        mock_boto3_client = MagicMock()
        monkeypatch.setattr(boto3, "client", mock_boto3_client)

        s3.get_s3_client()

        config = mock_boto3_client.call_args.kwargs["config"]
        assert config.connect_timeout == 2.5
        assert config.read_timeout == 2.5
        assert config.retries == {"total_max_attempts": 2, "mode": "standard"}
