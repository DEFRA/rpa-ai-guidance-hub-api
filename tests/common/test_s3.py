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
        )

    def test_returns_existing_cached_singleton_instance(self, monkeypatch):
        sentinel_client = object()
        monkeypatch.setattr(s3, "client", sentinel_client)

        result = s3.get_s3_client()

        assert result is sentinel_client
