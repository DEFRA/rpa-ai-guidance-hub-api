import pytest

from app.common import s3


@pytest.fixture(autouse=True)
def reset_s3_client():
    s3.client = None
    yield
    s3.client = None


def test_get_s3_client_creates_one_against_the_configured_endpoint(mocker):
    mock_config = mocker.Mock()
    mock_config.floci_endpoint_url = "http://floci:4566"
    mock_config.aws_region = "eu-west-2"
    mocker.patch("app.config.get_config", return_value=mock_config)

    mock_boto3_client = mocker.patch("app.common.s3.boto3.client")

    client = s3.get_s3_client()

    assert client == mock_boto3_client.return_value
    mock_boto3_client.assert_called_once_with(
        "s3", endpoint_url="http://floci:4566", region_name="eu-west-2"
    )


def test_get_s3_client_passes_none_endpoint_when_unconfigured(mocker):
    """No floci_endpoint_url: boto3 falls through to its normal AWS resolution,
    which is what talking to real AWS in CDP needs."""
    mock_config = mocker.Mock()
    mock_config.floci_endpoint_url = None
    mock_config.aws_region = "eu-west-2"
    mocker.patch("app.config.get_config", return_value=mock_config)

    mock_boto3_client = mocker.patch("app.common.s3.boto3.client")

    s3.get_s3_client()

    mock_boto3_client.assert_called_once_with(
        "s3", endpoint_url=None, region_name="eu-west-2"
    )


def test_get_s3_client_returns_existing(mocker):
    existing_client = mocker.Mock()
    s3.client = existing_client

    mock_boto3_client = mocker.patch("app.common.s3.boto3.client")

    result = s3.get_s3_client()

    assert result == existing_client
    mock_boto3_client.assert_not_called()
